from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json

import pytest

from xreactor import CoverGroup as CoreGroup, CoverageMergeError, Transfer, XEvent, XEventKind, XPhase, pytrigger, RisingEdge
from xreactor.declarative import Bin, CoverGroup, CoverPoint, DefinitionError, Fields, covergroup, wire
from examples.coverage.declarative_protocol import (
    CycleSnapshot, RequestSnapshot, ResponseSnapshot, RequestCycleCoverage, ReadyPoint,
    ToyResponder, TransactionCollector, TransactionCoverage, bind_pin_coverage, run,
)

MODES = [("memory", "python"), ("xcomm", "python"), ("xcomm", "native")]


def counts(result, category, point):
    return next(item["counts"] for item in result[category]["points"] if item["name"] == point)


def statistics(report):
    result = {key: report[key] for key in ("schema_digest", "samples", "gated", "coverage", "covered")}
    for category in ("points", "crosses"):
        result[category] = [{key: value for key, value in item.items() if key != "provenance"}
                            for item in report[category]]
    return result


@pytest.mark.parametrize("engine,strategy", MODES)
def test_pins_multicycle_and_tag_correlated_transactions(engine, strategy):
    result = asyncio.run(run(engine=engine, strategy=strategy))
    assert result["accepted"] == [(1, 3), (2, 4), (3, 5)]
    assert [item["tag"] for item in result["transactions"]] == [2, 1, 3]
    assert [item["latency"] for item in result["transactions"]] == [1, 3, 2]
    assert result["request_coverage"]["samples"] == 5
    assert result["roundtrip_coverage"]["samples"] == 1
    assert result["transaction_coverage"]["samples"] == 3
    assert counts(result, "request_coverage", "ready") == {
        "accepted": 3, "stalled": 2, "waited_two_then_accepted": 1,
    }
    assert counts(result, "transaction_coverage", "latency") == {
        "one_cycle": 1, "two_or_three": 2, "four_to_six": 0,
    }
    assert counts(result, "transaction_coverage", "out_of_order") == {"yes": 1, "no": 2}
    assert result["captures"][0]["request"]["ready"] is False
    assert result["captures"][-1]["request"]["ready"] is True
    assert result["captures"][0]["tick"] == 2


def test_all_three_paths_have_the_same_protocol_statistics():
    reports = [asyncio.run(run(engine=engine, strategy=strategy)) for engine, strategy in MODES]
    for category in ("request_coverage", "roundtrip_coverage", "transaction_coverage"):
        assert statistics(reports[0][category]) == statistics(reports[1][category]) == statistics(reports[2][category])
    assert reports[1]["roundtrip_coverage"]["sampling_contract"] == reports[2]["roundtrip_coverage"]["sampling_contract"]


@pytest.mark.parametrize("engine,strategy", MODES)
def test_temporal_window_expires_without_counting_a_different_tag(engine, strategy):
    result = asyncio.run(run(engine=engine, strategy=strategy, maximum=2))
    # Tag 2 responds one cycle later, but cannot finish tag 1's Sequence.
    assert result["roundtrip_coverage"]["samples"] == 0
    assert result["transaction_coverage"]["samples"] == 3


def test_temporal_program_changes_contract_and_prevents_report_merge():
    short = asyncio.run(run(engine="memory", strategy="python", maximum=2))["roundtrip_coverage"]
    long = asyncio.run(run(engine="memory", strategy="python", maximum=4))["roundtrip_coverage"]
    assert short["schema_digest"] == long["schema_digest"]
    assert short["sampling_contract"] != long["sampling_contract"]
    with pytest.raises(CoverageMergeError, match="contract"):
        CoreGroup.from_report(short).merge(CoreGroup.from_report(long))


@pytest.mark.parametrize("engine,strategy", MODES)
def test_wrong_observed_response_is_classified_as_incorrect(engine, strategy):
    result = asyncio.run(run(engine=engine, strategy=strategy, wrong=(2,)))
    assert counts(result, "transaction_coverage", "correct") == {"yes": 2, "no": 1}
    assert result["transactions"][0]["tag"] == 2 and not result["transactions"][0]["correct"]


@pytest.mark.parametrize("engine,strategy", [("memory", "python"), ("xcomm", "native")])
def test_missing_response_does_not_create_a_completed_transaction(engine, strategy, monkeypatch):
    closed = []
    original = ToyResponder.close
    def close(toy):
        assert toy.backend.watcher_count == 0
        if toy.native:
            assert toy.backend._engine.ActiveCount() == 0
        original(toy)
        closed.append(toy)
    monkeypatch.setattr(ToyResponder, "close", close)
    with pytest.raises(ValueError, match="responses missing for tags.*1"):
        asyncio.run(run(engine=engine, strategy=strategy, drop=(1,)))
    assert len(closed) == 1


@pytest.mark.parametrize("engine,strategy", [("memory", "python"), ("xcomm", "native")])
def test_bad_temporal_definition_closes_the_example_backend(engine, strategy, monkeypatch):
    closed = []
    original = ToyResponder.close
    def close(toy):
        assert toy.backend.watcher_count == 0
        original(toy)
        closed.append(toy)
    monkeypatch.setattr(ToyResponder, "close", close)
    with pytest.raises(ValueError):
        asyncio.run(run(engine=engine, strategy=strategy, maximum=0))
    assert len(closed) == 1


def observation(cycle, *, request=None, response=None, valid=True, phase=XPhase.RISING_STABLE):
    req = RequestSnapshot(request is not None and valid, request is not None,
                          0 if request is None else request[0], 0 if request is None else request[1])
    rsp = ResponseSnapshot(response is not None, True,
                           0 if response is None else response[0], 0 if response is None else response[1])
    return Transfer(XEvent(cycle, cycle * 2, phase, XEventKind.CLOCK_RISE), CycleSnapshot(req, rsp))


def test_collector_accepts_same_cycle_retirement_and_tag_reuse():
    collector = TransactionCollector()
    assert collector.observe(observation(1, request=(1, 0))) is None
    assert collector.observe(observation(2, request=(2, 1))) is None
    first = collector.observe(observation(3, request=(1, 1), response=(1, 10)))
    second = collector.observe(observation(4, response=(2, 21)))
    third = collector.observe(observation(5, response=(1, 11)))
    assert [item.opcode for item in (first, second, third)] == [0, 1, 1]
    assert all(item.latency == 2 and item.correct for item in (first, second, third))
    collector.finish()


def test_collector_rejects_unknown_response_and_duplicate_live_tag():
    collector = TransactionCollector()
    with pytest.raises(ValueError, match="unknown tag"):
        collector.observe(observation(1, response=(7, 70)))
    collector.observe(observation(1, request=(1, 0)))
    with pytest.raises(ValueError, match="duplicate accepted tag"):
        collector.observe(observation(2, request=(1, 0)))
    with pytest.raises(ValueError, match="responses missing"):
        collector.finish()


def test_collector_rejects_duplicate_frame_and_wrong_phase():
    collector = TransactionCollector()
    collector.observe(observation(1, request=(1, 0)))
    with pytest.raises(ValueError, match="distinct increasing"):
        collector.observe(observation(1))
    with pytest.raises(ValueError, match="rising-stable"):
        collector.observe(observation(2, phase=XPhase.FALLING_STABLE))


def test_transaction_coverage_waits_for_the_observed_response():
    collector = TransactionCollector()
    coverage = TransactionCoverage(instance="observed")
    for item in (observation(1, request=(1, 0)), observation(2)):
        completed = collector.observe(item)
        if completed is not None:
            coverage.sample(completed)
    assert coverage.samples == 0
    coverage.sample(collector.observe(observation(3, response=(1, 10))))
    assert coverage.samples == 1


def test_gate_breaks_a_transition_across_invalid_cycles():
    coverage = RequestCycleCoverage(instance="gate")
    for valid, ready in ((True, False), (False, False), (True, False), (True, True)):
        coverage.sample(CycleSnapshot(RequestSnapshot(valid, ready, 1, 0), ResponseSnapshot(False, True, 0, 0)))
    assert coverage.ready.count(ReadyPoint.waited_two_then_accepted) == 0


def test_binding_export_contains_the_actual_temporal_window():
    toy = ToyResponder(engine="memory")
    try:
        _, coverage = bind_pin_coverage(toy.pins, strategy="python", maximum=2)
        contract = json.loads(coverage.binding_contract_json())
        assert contract["sampling"]["program"]["steps"][1]["maximum"] == 2
    finally:
        toy.close()


def test_python_predicate_requires_an_explicit_semantic_contract():
    @dataclass(frozen=True)
    class Sample:
        value: int
    class Point(CoverPoint[int]):
        zero = Bin.values(0)
    @covergroup(schema_id="predicate")
    class Model(CoverGroup[Sample]):
        value = Point()
    toy = ToyResponder(engine="memory")
    @pytrigger(sample=RisingEdge("clock"))
    def predicate(pins):
        return pins.response.valid.U() != 0
    try:
        group = Model(instance="predicate")
        with pytest.raises(DefinitionError, match="explicit observer contract"):
            group.bind(trigger=predicate(toy.pins), fields=(
                wire(Fields(Sample).select(lambda s: s.value), toy.pins.response.tag, source_id="dut.response.tag"),
            ))
    finally:
        toy.close()
