import asyncio

import pytest

from examples.transactions import reference_model as example
from toffee import ScoreboardMismatch


def test_reference_model_is_unit_testable_without_execution():
    model = example.AccumulatorModel(initial=10)
    assert model.accept(example.Command(1, 3)) == example.Result(1, 13)
    assert model.accept(example.Command(2, 5)) == example.Result(2, 18)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "native"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_model_uses_accepted_inputs_despite_response_reordering(backend, cancel):
    if backend == "native":
        pytest.importorskip("xspcomm")
    tasks = asyncio.all_tasks()
    async with asyncio.timeout(2):
        results, order, total = await example.run_example(backend, cancel_pending=cancel)
    assert results == ([example.Result(1, 3), example.Result(2, 8)] + ([] if cancel else [example.Result(3, 15)]))
    assert order == ([2, 1] if cancel else [2, 1, 3])
    assert total == (8 if cancel else 15)
    assert asyncio.all_tasks() == tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "native"])
async def test_model_detects_dut_fault_and_agent_releases_resources(backend, monkeypatch):
    if backend == "native":
        pytest.importorskip("xspcomm")
    instances = []
    original = example.AccumulatorDut

    def make(*args, **kwargs):
        dut = original(*args, **kwargs)
        instances.append(dut)
        return dut

    monkeypatch.setattr(example, "AccumulatorDut", make)
    tasks = asyncio.all_tasks()
    async with asyncio.timeout(2):
        with pytest.raises(ScoreboardMismatch) as caught:
            await example.run_example(backend, corrupt=True)
    assert caught.value.expected == example.Result(2, 8)
    assert caught.value.actual == example.Result(2, 9)
    dut, = instances
    assert dut.backend.watcher_count == 0 and dut.backend._owner is None
    assert dut.tag.U() == dut.operand.U() == 0
    assert asyncio.all_tasks() == tasks
