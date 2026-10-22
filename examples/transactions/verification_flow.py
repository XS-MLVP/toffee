"""Interface instances, model connection and centrally owned run lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import platform

from examples.transactions.pipeline import Input, Response, ToyPipeline
from toffee import (
    Agent, Bin, CoverageDatabase, CoverGroupDef, CoverPointDef, Execution,
    generate_unified_coverage_report,
)


class CoveredMonitor:
    """One consumer: sample each delivered observation, then forward it unchanged."""

    def __init__(self, source, coverage):
        self.source = source
        self.coverage = coverage

    def start(self, execution):
        self.source.start(execution)
        return self

    async def recv(self):
        observation = await self.source.recv()
        self.coverage.sample(
            {"tag": observation.value.tag},
            metadata={"tick": observation.event.tick},
        )
        return observation

    async def aclose(self):
        await self.source.aclose()


class PipelineModel:
    def __init__(self, expected=None):
        self.expected = expected or (lambda request: Response(request, request * 10))

    def accept(self, request):
        return self.expected(request)


class PipelineAgent(Agent[Input]):
    def __init__(self, toy, coverage, *, response_timeout_cycles):
        super().__init__(
            "pipeline-port", driver=Input(toy),
            monitors={"output": CoveredMonitor(toy.monitor, coverage)},
            response_monitor="output", clock=toy.clock,
            response_timeout_cycles=response_timeout_cycles,
        )


@asynccontextmanager
async def verified_pipeline(
    *, output: Path, nodeid: str, seed: int, backend: str = "memory",
    response_timeout_cycles: int = 6, wall_timeout_seconds: float = 2,
    max_settle_rounds: int = 100_000, expected=None,
):
    """Own one toy DUT run and save observations even when checking fails.

    The test supplies its finish budget and observation window explicitly.
    Seed belongs to test stimulus generation; this deterministic DUT uses none.
    ``output`` must be a fresh directory, preventing cross-run overwrites.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    coverage = CoverGroupDef(
        "responses",
        (CoverPointDef("tag", {str(tag): Bin.values(tag) for tag in (1, 2, 3)}),),
    ).instantiate(
        "pipeline.output", run_id=str(output.resolve()),
        run_metadata={"test": nodeid, "seed": seed, "backend": backend,
                      "output": str(output.resolve())},
        contract="pipeline.output-observation-before-scoreboard/v1",
    )
    database = CoverageDatabase([coverage])
    metadata = {
        "nodeid": nodeid, "seed": seed, "backend": backend,
        "python": platform.python_version(), "state": "initializing",
        "response_timeout_cycles": response_timeout_cycles,
        "wall_timeout_seconds": wall_timeout_seconds,
        "max_settle_rounds": max_settle_rounds,
        "sampling": "observations delivered to scoreboard, before comparison",
        "output": str(output.resolve()),
    }
    toy = None
    primary = None
    try:
        toy = ToyPipeline(backend=backend)
        agent = PipelineAgent(toy, coverage, response_timeout_cycles=response_timeout_cycles)
        agent.connect(PipelineModel(expected))
        async with (
            asyncio.timeout(wall_timeout_seconds),
            Execution(toy.backend, agents=[agent], max_settle_rounds=max_settle_rounds),
        ):
            metadata["state"] = "running"
            yield agent
        metadata["state"] = "closed"
    except BaseException as error:
        primary = error
        metadata.update(state="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        errors = []
        if toy is not None:
            metadata["last_tick"] = toy.backend.tick
            try:
                toy.close()
            except BaseException as error:
                errors.append(error)
        functional = output / "functional.json"
        try:
            database.write_json(functional)
        except Exception as error:
            errors.append(error)
        else:
            try:
                generate_unified_coverage_report(
                    functional_paths=[functional], output=output / "coverage.html",
                )
            except Exception as error:
                errors.append(error)
        if errors:
            metadata.update(state="failed", cleanup_errors=[str(error) for error in errors])
        try:
            (output / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
        except Exception as error:
            errors.append(error)
        if errors:
            # Keep the original checking/cancellation error and every independent
            # export/cleanup failure. The recipe never replaces a failed verdict.
            if primary is not None:
                errors.insert(0, primary)
            if len(errors) == 1:
                raise errors[0]
            raise BaseExceptionGroup("verification and artifact failures", errors)
