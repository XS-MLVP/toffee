# Define coverage models with classes

Small models can use `CoverGroupDef` directly, as shown in [functional coverage](coverage.md).
For larger models or shared binning rules, use class declarations from `toffee.declarative`.
Both forms share counting, merging, and reporting; no separate runner is needed.

## Samples, coverpoints, and covergroups

A sample stores one actual observation. A coverpoint classifies it into situations called bins.
A covergroup collects related points and can count their cross combinations.

This example runs as written:

```python
from dataclasses import dataclass
from toffee.declarative import Bin, CoverGroup, CoverPoint, Cross, covergroup, coverpoint


@dataclass(frozen=True)
class Sample:
    state: int
    taken: bool


@coverpoint
class StatePoint(CoverPoint[int]):
    idle = Bin.values(0)
    busy = Bin.values(1, at_least=2)
    done = Bin.values(2)
    path = Bin.transition(0, 1, 2)


@coverpoint
class TakenPoint(CoverPoint[bool]):
    yes = Bin.values(True)
    no = Bin.values(False)


@covergroup(schema_id="pipeline.activity", contract="observed-pipeline/v1")
class PipelineCoverage(CoverGroup[Sample]):
    state = StatePoint()
    taken = TakenPoint()
    state_taken = Cross(state, taken)


coverage = PipelineCoverage(instance="pipeline0")
coverage.sample(Sample(state=0, taken=False))
coverage.sample(Sample(state=1, taken=True))
coverage.sample(Sample(state=2, taken=True))

assert coverage.state.count(StatePoint.busy) == 1
assert coverage.state.count(StatePoint.path) == 1
```

`busy` requires two hits but receives only one, so the model has not reached full coverage.
`path` requires three adjacent samples with values 0, 1, 2; it does not search for all three values
inside a single sample.

## Where samples come from

Manual `sample()` suits snapshots or completed transactions already captured by a Monitor.
Use actual observations: intending to test a busy state does not justify sampling `state=1`.

Fields use matching names by default: `state` reads `Sample.state`. Select a different or nested
field explicitly with `Fields(Sample).select(lambda s: s.state)`. The entire sample is type-checked;
`bool` cannot substitute for an `int` field.

## Sample signals automatically

Given bound `dut.state`, `dut.taken`, and `clock` signals:

```python
from toffee import RisingEdge
from toffee.declarative import Fields, wire

fields = Fields(Sample)
coverage.bind(
    trigger=RisingEdge(clock),
    fields=(
        wire(fields.select(lambda s: s.state), dut.state, source_id="dut.state"),
        wire(fields.select(lambda s: s.taken), dut.taken, source_id="dut.taken"),
    ),
    strategy="native",
)
```

Pass `coverage.runtime` to `Execution(backend, coverage=[coverage.runtime])` to start sampling on
entry. `source_id` identifies each field's signal for binding review and report merging.
Automatic signal sampling supports `int` and `bool`; booleans require one-bit signals.

`strategy="native"` requires native execution and rejects unsupported bindings. `"auto"` permits
whole-group Python fallback and records its reason in the report.

## Reuse models and inspect definitions

Subclasses can add bins to a coverpoint, and a covergroup can replace its original point with
that subclass. Existing instances and base classes remain unchanged. Inherited crosses expand
against the new model. Combine independent declarations with `CoverageFragment[Sample]`.

After compilation, do not mutate class attributes or sample annotations; define a new class or
subclass instead. `PipelineCoverage.compile().explain()` lists fields, bins, targets, and crosses.
`diff()` compares compiled models. Changing definitions or sampling rules may make older reports
incompatible for merging.

## Run the complete example

From the repository root:

```bash
python3 -m examples.coverage.declarative_pipeline --backend manual
python3 -m examples.coverage.declarative_pipeline --backend native --output-dir output/classes
```

See [declarative_pipeline.py](https://github.com/XS-MLVP/toffee/blob/main/examples/coverage/declarative_pipeline.py).
For latency and out-of-order behavior by request ID, see [transaction coverage](coverage-transactions.md).
For conditions spanning cycles, see [temporal coverage](coverage-patterns.md).
