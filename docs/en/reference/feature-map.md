# Features, scenarios, and regressions

This index connects user needs to guides, runnable examples, and behavior regressions.
Test links identify relevant groups and what they check; file existence and test counts are not
substitutes for behavioral verification.

| Feature | Scenario and guide | Example / corresponding tests |
| --- | --- | --- |
| Getting started | Assertions, clocks, boundaries, components, models, pipelining, failures, coverage | [Tutorial](../getting-started/index.md); examples/getting_started joins default regression; test_quickstart_tutorial checks snippets and failure exercises; native_lab runs explicitly |
| Simulation lifecycle | [Execution](../guides/execution-and-backend.md): Backend reuse, pauses, exclusive lease, settling, cleanup | [runtime](https://github.com/XS-MLVP/toffee/blob/main/tests/runtime/test_runtime.py), [observer](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_asyncio_observer.py): order, cancellation, external-task isolation, non-settling errors |
| Clocks and conditions | [Triggers](../guides/triggers.md): stable phases, default sampling, enter/change/each_sample, ValueChange, simulation/wall timeouts | [basic_execution](https://github.com/XS-MLVP/toffee/blob/main/examples/triggers/basic_execution.py), [native regression](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_xcomm_backend.py): real XClock, wide signals, X/Z, registration baselines, phases |
| Composed events and external inputs | AnyOf/AllOf, Queue/Event/TaskComplete, [explicit HTTP/external-task synchronization](../guides/asyncio-and-pytest.md) | [adapters](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_asyncio_adapters.py), [HTTP](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_http_integration.py): composition, timeouts, shield ownership |
| Compiled patterns | [Conditions and Sequence/FSM](../guides/compiled-triggers.md): compilation, windows, holds, restart, branch priority | [runtime](https://github.com/XS-MLVP/toffee/blob/main/tests/runtime/test_runtime.py), [native](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_xcomm_backend.py): matching, caching, native execution; [pattern boundaries](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_compiled_patterns.py): windows, expiry restart, interrupted holds |
| Persistent subscriptions | [Synchronous capture and async handlers](../guides/subscriptions.md), no registration gaps, lossless/latest, errors | [example](https://github.com/XS-MLVP/toffee/blob/main/examples/triggers/subscription.py), [regression](https://github.com/XS-MLVP/toffee/blob/main/tests/runtime/test_subscriptions.py): snapshots, order, capacity, failure cleanup |
| Data and binding | [Bundle/Field/packed](../guides/data.md): snapshots, widths, metadata, dimensions, bit order, stride, offset, four-state data | [data regression](https://github.com/XS-MLVP/toffee/blob/main/tests/interfaces/test_data_interfaces.py): shape, direction, signal identity; native tests: live subviews, padding, widths |
| Drivers | [Templates](../guides/drivers.md): synchronous calls/immediate submission, single/fixed-stage timing, capacity/max_active, locks, idle, cancellation | [pipeline examples](../guides/pipeline-examples.md); [Sync](https://github.com/XS-MLVP/toffee/blob/main/tests/interfaces/test_sync_driver.py), [Async](https://github.com/XS-MLVP/toffee/blob/main/tests/interfaces/test_async_driver.py), [staged](https://github.com/XS-MLVP/toffee/blob/main/tests/interfaces/test_staged_driver.py), [handoffs](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_driver_handoffs.py): acceptance ticks, overlap, exclusion, fairness, no bubbles, cleanup |
| Monitors | [Sampling and passive observations](../guides/monitors.md): explicit triggers, immutable records, capacity, closing, failures | [sampling](https://github.com/XS-MLVP/toffee/blob/main/tests/interfaces/test_sampling_monitor.py), [lifecycle](https://github.com/XS-MLVP/toffee/blob/main/tests/interfaces/test_monitor_lifecycle.py): both built-in Monitors, both Backends, all-waiter wakeup |
| Agents and models | [Instances and connect](../guides/agents-and-reference-models.md): Driver kinds, state updates, shared models, batches, one consumer, lifecycle | [stateful example](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/reference_model.py), [native integration](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_agent_native.py), [unit cleanup](https://github.com/XS-MLVP/toffee/blob/main/tests/interfaces/test_agent.py): acceptance, expected overrides, unawaited errors, startup rollback |
| Scoreboard and transactions | [Direct and response checking](../guides/scoreboard.md): differences, FIFO/key/fixed latency, cycle windows, drain/finish, cancellation | [example](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/scoreboard.py), [association](https://github.com/XS-MLVP/toffee/blob/main/tests/runtime/test_scoreboard.py), [XTransfer](https://github.com/XS-MLVP/toffee/blob/main/tests/runtime/test_transfers.py), [native deadlines](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_transaction_lifecycle_native.py): missing/late/duplicate responses, response before acceptance notification, deadline priority, active-table release |
| Optional protocol helpers | [ReadyValid/Decoupled](../guides/protocols.md), role views, ReadyValidMonitor, drive helper | [protocols](https://github.com/XS-MLVP/toffee/blob/main/tests/interfaces/test_protocols.py), data/native regressions: handshake acceptance, backpressure, candidate sampling, cleanup |
| Functional coverage | [Requirements across cases](../guides/functional-points.md), [bins/crosses/gates/goals](../guides/coverage.md), illegal hits, snapshot fields, atomic sampling, persistence/merge/reset | [example](https://github.com/XS-MLVP/toffee/blob/main/examples/coverage/functional_coverage.py), [regression](https://github.com/XS-MLVP/toffee/blob/main/tests/coverage/test_coverage.py): hit priority, gates, overlap, cross limits, weighting, schema, merge |
| Reports and artifacts | [Run recipe](../guides/verification-flow.md): seeds, budgets, one-consumer coverage, failure export, JSON/HTML/LCOV | [recipe tests](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/test_pipeline_coverage.py), [artifacts](https://github.com/XS-MLVP/toffee/blob/main/tests/integration/test_verification_flow.py), [reports](https://github.com/XS-MLVP/toffee/blob/main/tests/coverage/test_coverage_report.py): failure retention, counts, escaping, directory protection, source pages |

## Run the corresponding regressions

After installing the test extra from the repository root, `python3 -m pytest -q` runs framework
regressions, the beginner tutorial, and artifact examples. Native cases skip without xspcomm;
releases and complete acceptance must use:

```bash
python3 -m pytest -q --require-xspcomm
mkdocs build --strict
```

To check a feature, pass its test file, for example:

```bash
python3 -m pytest -q tests/interfaces/test_agent.py tests/integration/test_agent_native.py --require-xspcomm
python3 -m pytest -q tests/coverage
```

MemoryBackend tests check Python semantics; native XClock tests check actual Backend sampling
and scheduling. Neither replaces the other. Real DUT build/artifact dependencies such as e203/cache
are outside the default regression; see [project examples](../examples.md). This table does not
claim arbitrary RTL has been verified.

New capabilities should update their guide, minimal runnable scenario, and behavior regression.
Prose-only edits do not need tests mirroring implementation. See [API](api.md) for exports and
[limitations](semantics-and-limitations.md) for supported boundaries.
