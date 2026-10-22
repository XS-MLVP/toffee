"""Check the project recipe's ownership and artifact paths on both backends."""

import asyncio
import json

import pytest

from examples.transactions.pipeline import Response
from examples.transactions import verification_flow as recipe
from toffee import CoverageDatabase, ScoreboardMismatch


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "native"])
@pytest.mark.parametrize("outcome", ["pass", "mismatch", "body", "cancel", "teardown", "start"])
async def test_recipe_preserves_observations_and_releases_resources(tmp_path, backend, outcome, monkeypatch):
    if backend == "native":
        pytest.importorskip("xspcomm")
    toys = []
    original = recipe.ToyPipeline
    failure = RuntimeError(f"injected {outcome}")

    def make_toy(**kwargs):
        toy = original(**kwargs)
        toys.append(toy)
        if outcome == "start":
            def start(execution):
                raise failure
            toy.monitor.start = start
        if outcome == "teardown":
            close = toy.close
            def broken_close():
                close()
                raise failure
            toy.close = broken_close
        return toy

    monkeypatch.setattr(recipe, "ToyPipeline", make_toy)
    before = asyncio.all_tasks()
    output = tmp_path / "run"
    caught = None
    try:
        async with recipe.verified_pipeline(
            output=output, nodeid="test_recipe", seed=17, backend=backend,
            expected=(lambda request: Response(request, -1)) if outcome == "mismatch" else None,
        ) as board:
            transfer = board.submit(1)
            await transfer
            await board.finish(timeout_cycles=20, observe_cycles=1)
            if outcome == "body":
                raise failure
            if outcome == "cancel":
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
    except BaseException as error:
        caught = error
    if outcome == "pass":
        assert caught is None
    elif outcome == "mismatch":
        assert isinstance(caught, ScoreboardMismatch)
    elif outcome == "cancel":
        assert isinstance(caught, asyncio.CancelledError)
    else:
        assert caught is failure
    database = CoverageDatabase.read_json(output / "functional.json")
    group = database["pipeline.output"].report()
    assert group["samples"] == (0 if outcome == "start" else 1)
    assert group["points"][0]["counts"]["1"] == (0 if outcome == "start" else 1)
    origin = group["active_origin"]
    assert group["origins"][origin]["run_id"] == str(output.resolve())
    assert group["origins"][origin]["metadata"]["test"] == "test_recipe"
    assert group["origins"][origin]["metadata"]["seed"] == 17
    if outcome != "start":
        proof = group["points"][0]["provenance"]["1"][origin]
        assert proof["count"] == 1 and proof["first"]["tick"] > 0
    assert "xreactor-unified-coverage" in (output / "coverage.html").read_text()
    metadata = json.loads((output / "run.json").read_text())
    assert metadata["state"] == ("closed" if outcome == "pass" else "failed")
    assert metadata["nodeid"] == "test_recipe" and metadata["seed"] == 17
    assert asyncio.all_tasks() == before
    toy, = toys
    assert toy.backend.watcher_count == 0
    assert toy.backend._owner is None
    assert toy.first.U() == toy.second.U() == 0
    if toy.native:
        assert toy.clock.StepRisQueueSize() == 0


@pytest.mark.asyncio
async def test_export_failure_keeps_checking_failure_and_other_artifacts(tmp_path, monkeypatch):
    export_error = OSError("HTML output failed")

    def fail_export(**kwargs):
        raise export_error

    monkeypatch.setattr(recipe, "generate_unified_coverage_report", fail_export)
    output = tmp_path / "run"
    with pytest.raises(BaseExceptionGroup) as caught:
        async with recipe.verified_pipeline(
            output=output, nodeid="bad-check", seed=1,
            expected=lambda request: Response(request, -1),
        ) as board:
            await board.submit(1)
    assert isinstance(caught.value.exceptions[0], ScoreboardMismatch)
    assert caught.value.exceptions[1] is export_error
    assert CoverageDatabase.read_json(output / "functional.json")["pipeline.output"].report()["samples"] == 1
    assert json.loads((output / "run.json").read_text())["cleanup_errors"] == [str(export_error)]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "native"])
async def test_partial_runs_contribute_to_shared_coverage(tmp_path, backend):
    if backend == "native":
        pytest.importorskip("xspcomm")
    combined = CoverageDatabase()
    for name, requests in (("first", (1, 2)), ("second", (2, 3))):
        output = tmp_path / name
        async with recipe.verified_pipeline(
            output=output, nodeid=name, seed=17, backend=backend,
        ) as agent:
            transfers = [agent.submit(request) for request in requests]
            await agent.finish(timeout_cycles=30, observe_cycles=1)
            assert [response.tag for response in [await item for item in transfers]] == list(requests)
        individual = CoverageDatabase.read_json(output / "functional.json")
        assert individual.coverage == pytest.approx(200 / 3)
        combined.merge(individual)
    group = combined["pipeline.output"]
    group.assert_coverage(100)
    assert group.report()["points"][0]["counts"] == {"1": 1, "2": 2, "3": 1}
    assert group.report()["samples"] == 4
