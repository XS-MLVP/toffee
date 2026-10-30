# Assemble components and save run artifacts

This page assumes you already have a Driver, Monitor, and model, and want a runnable environment
that saves its results. For why sampling location changes counts, see
[checking, coverage, and regression results](../explanation/coverage-results.md).

This recipe connects Driver, Monitor, Scoreboard, and coverage. Cases submit transactions and call
a budgeted finish. The environment constructs an Agent and connects an independent model;
Execution manages components, and the Agent connects its response Scoreboard. See
[Agents and reference models](agents-and-reference-models.md) for boundaries and stateful models.

Runnable sources: [environment implementation](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/verification_flow.py)
and [pytest cases and fixture](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/test_pipeline_coverage.py).

```bash
python3 -m pytest -q -s examples/transactions/test_pipeline_coverage.py
```

The fixture uses pytest's per-test temporary directory, with seed and run-number subdirectories,
and prints each artifact path. Existing run directories cannot be overwritten. Each run saves
`functional.json`, `coverage.html`, and `run.json`. The latter records node ID, seed, backend,
Python version, budgets, final tick, run state, and exception. The two cases contribute tags 1/2
and 2/3 respectively: each reaches 2/3, and the combined result reaches 3/3. See
[cross-case coverage](functional-points.md) for merging; test execution uses native pytest reports.

## Assign one lifecycle owner

| Object | Creator and closer | Shutdown action |
| --- | --- | --- |
| DUT substitute and Backend | Project recipe | Close after components and Execution exit |
| Execution | Project context | Release advancement task, watchers, and backend lease |
| Driver | Agent inside Execution | Clean up input tasks and signal ownership |
| Monitor | Agent; consumed exclusively by its Scoreboard | Start and stop with Agent |
| Reference model | Project environment | Agent only calls accept; it does not reset or close a shared model |
| Coverage database | Project recipe | Save in finally, including failures and cancellation |

Execution starts `agents=[agent]` and cleans them up in reverse order. Agent itself is not a
context manager. Startup failure also cleans already-started components. DUT constructors that
acquire resources in stages must clean up incomplete construction themselves. This example uses
the deterministic ToyPipeline substitute; it does not verify real RTL initialization, reset, or flush.

## Sample each observation once

`CoveredMonitor` is a small project adapter around the single recv entry point. It samples before returning:

```python
async def recv(self):
    observation = await self.source.recv()
    self.coverage.sample(
        {"tag": observation.value.tag},
        metadata={"tick": observation.event.tick},
    )
    return observation
```

The Scoreboard receives the same immutable Transfer. The adapter has no queue, background task,
or second consumer. This protocol-independent example reuses ToyPipeline's transaction substitute.
For real signals, replace its source with `SamplingMonitor(output_event, capture=...)` or a project
protocol Monitor.

The chosen sampling location is the DUT observation before delivery to the checker. A response
still counts if its subsequent comparison fails. Observations not delivered after checking stops
do not count. These are therefore neither counts of successfully checked transactions nor a
complete record of DUT activity after failure. To count accepted inputs or successful comparisons,
choose that location explicitly instead of calling recv again on the same Monitor.

Keep pytest outcomes, functional bins, and simulator line coverage separate. This example collects
functional bins only; project tooling exports simulator LCOV. `CoverageDatabase.merge()` combines
compatible schemas and rejects incompatible ones.

## Configure three distinct budgets

```python
async with pipeline(seed=17) as agent:
    transfers = [agent.submit(value) for value in (1, 2, 3)]
    await agent.finish(timeout_cycles=30, observe_cycles=1)
    results = [await transfer for transfer in transfers]
```

The recipe explicitly sets a per-transaction response-cycle budget and an overall wall-clock limit.
Each case supplies its finish-cycle budget and trailing observation window. `Execution.max_settle_rounds`
only detects non-converging immediate work within a phase; it does not replace those timeouts.
The recipe implements its wall-clock limit with asyncio.timeout, so cases need no asyncio primitives.

Cases use local `random.Random(seed)` instances without changing global random state. The recipe
records the seed but does not infer DUT initialization or manage a seed matrix. `run.json` describes
this simulation context; assertions outside it and other fixture teardown results remain governed
by pytest's native result.

Checking failure, external cancellation, and cleanup failure all trigger artifact saving. Export
failure is retained together with an existing verification exception and cannot turn failure into
success. Unwritable directories cannot guarantee artifacts; an explicit exception reports saving failure.

Regressions on MemoryBackend and native XClock cover success, mismatch, test-body exceptions,
external cancellation, startup failure, and cleanup failure. They check watchers, backend leases,
idle signals, and task release. HTML export failure has a separate regression. This recipe does
not aggregate pytest-xdist/reruns or manage simulator processes.
