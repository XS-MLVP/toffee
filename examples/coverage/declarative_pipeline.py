"""Runnable coverage v2 example: no Enum and no field-name strings."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path

from xreactor.declarative import (
    Bin, CoverGroup, CoverPoint, Cross, IllegalPolicy, covergroup, coverpoint,
    Fields, wire,
)


@dataclass(frozen=True)
class PipelineSample:
    state: int
    taken: bool


@coverpoint
class StatePoint(CoverPoint[int]):
    idle = Bin.values(0)
    busy = Bin.values(1, at_least=2)
    done = Bin.values(2)
    short = Bin.range(3, 5)
    idle_to_done = Bin.transition(0, 1, 2)
    ignored = Bin.ignore(Bin.values(254))
    illegal = Bin.illegal(Bin.values(255))
    other = Bin.default(int)


@coverpoint
class TakenPoint(CoverPoint[bool]):
    yes = Bin.values(True)
    no = Bin.values(False)


@covergroup(schema_id="bpu.pipeline", contract="bpu.observed-pipeline/v1")
class PipelineCoverage(CoverGroup[PipelineSample]):
    state = StatePoint()
    taken = TakenPoint()
    state_taken = Cross(state, taken)


class SplitStatePoint(StatePoint):
    short = Bin.range(3, 4)
    extra = Bin.values(5)


class SplitPipelineCoverage(PipelineCoverage):
    state = SplitStatePoint()


TRACE = tuple(PipelineSample(state, bool(index & 1))
              for index, state in enumerate((0, 1, 2, 1, 1, 3, 5, 254, 255, 7)))


async def run_clocked(strategy: str) -> PipelineCoverage:
    import xspcomm
    from xreactor import ClockCycles, Execution, RisingEdge, XCommClockBackend
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    state = xspcomm.XData(8, xspcomm.XData.InOut)
    taken = xspcomm.XData(1, xspcomm.XData.InOut)
    fields = Fields(PipelineSample)
    coverage = PipelineCoverage(instance="core0.bpu", illegal_policy=IllegalPolicy.RECORD)
    coverage.bind(trigger=RisingEdge(clock), strategy=strategy, fields=(
        wire(fields.select(lambda s: s.state), state, source_id="dut.state"),
        wire(fields.select(lambda s: s.taken), taken, source_id="dut.taken"),
    ))
    try:
        async with Execution(backend, coverage=[coverage.runtime]):
            for observed in TRACE:
                state.Set(observed.state)
                taken.Set(int(observed.taken))
                await ClockCycles(clock, 1)
        return coverage
    finally:
        backend.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--backend", choices=("manual", "python", "native", "auto"), default="manual")
    args = parser.parse_args()
    model = PipelineCoverage.compile()
    split = SplitPipelineCoverage.compile()
    if args.backend == "manual":
        coverage = PipelineCoverage(instance="core0.bpu", illegal_policy=IllegalPolicy.RECORD)
        for observed in TRACE:
            coverage.sample(observed)
    else:
        coverage = asyncio.run(run_clocked(args.backend))
    print(model.explain())
    print(f"\nsamples={coverage.snapshot().samples}, busy={coverage.state.count(StatePoint.busy)}")
    print(f"backend={coverage.report()['sampling_backend']}")
    print("\nDerived-model changes:")
    print(json.dumps(model.diff(split), ensure_ascii=False, indent=2))
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        artifacts = {
            "base-model.txt": model.explain(),
            "derived-model.txt": split.explain(),
            "model-diff.json": json.dumps(model.diff(split), ensure_ascii=False, indent=2),
            "schema.json": model.schema_json(),
            "input-contract.json": model.input_contract_json(),
            "binding-contract.json": coverage.binding_contract_json() or "null",
            "report.json": coverage.snapshot().to_json(),
        }
        for name, content in artifacts.items():
            (args.output_dir / name).write_text(content + "\n")


if __name__ == "__main__":
    main()
