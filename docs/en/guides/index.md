# How-to guides

These guides are organized around project tasks. New users should start with the
[accumulator tutorial](../getting-started/index.md); defaults and constraints belong in the [reference](../reference/index.md).

## Connect and run simulation

| Task | Guide |
| --- | --- |
| Create a backend, start tests, and reuse fixtures | [Execution and Backend](execution-and-backend.md) |
| Wait for clocks, signals, timeouts, or external events | [Triggers](triggers.md) |
| Recognize conditions across cycles | [Compiled triggers, Sequence, and FSM](compiled-triggers.md) |
| Receive persistent notifications and capture snapshots | [Subscriptions](subscriptions.md) |
| Integrate HTTP, queues, and background services | [asyncio and pytest](asyncio-and-pytest.md) |

## Organize verification components

| Task | Guide |
| --- | --- |
| Bind DUT signals to structured objects | [Signals and Bundles](data.md) |
| Drive single-cycle, multicycle, or overlapping inputs | [Drivers](drivers.md) |
| Capture outputs without missing brief responses | [Monitors](monitors.md) |
| Connect components and stateful models | [Agents and reference models](agents-and-reference-models.md) |
| Compare and associate transactions | [Scoreboards](scoreboard.md) |
| Use ready/valid channel views | [Protocols](protocols.md) |
| Run consecutive and overlapping input stages | [Pipeline examples](pipeline-examples.md) |

## Coverage and regression

| Task | Guide |
| --- | --- |
| Define bins, crosses, and goals | [Functional coverage](coverage.md) |
| Reuse class-based coverage models | [Coverage classes](coverage-classes.md) |
| Measure latency and reordering | [Transaction coverage](coverage-transactions.md) |
| Count temporal events | [Temporal coverage](coverage-patterns.md) |
| Turn requirements into observable scenarios | [Functional points](functional-points.md) |
| Connect checks and coverage to an environment | [Verification flow](verification-flow.md) |
| Run parallel tests and diagnose reports | [Runner](runner.md) |

## Migration and maintenance

- [Migrate to Toffee 2](toffee-v2-migration.md) from XReactor or Toffee Legacy.
- [Versions, CI, and releases](versioning-and-releases.md) describes version selection and publication checks.

For complete programs, use the [example index](../examples.md).
