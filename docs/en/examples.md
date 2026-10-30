# Examples

Run these commands from the repository root after `python3 -m pip install -e '.[test]'`.

## Accumulator tutorial from first principles

The [learning path](getting-started/index.md) starts with four assertion lines, then adds clocks,
boundaries, Drivers, Monitors, Agents, reference models, pipelining, failure diagnosis, and coverage.
Every lesson explains new code, expected results, and exercises. Mainline code lives in
`test_accumulator.py`; `toy_dut.py` supplies a teaching DUT without native bindings or RTL.

```bash
python3 -m pytest -q examples/getting_started
```

The unmodified examples should report `16 passed`. Individual commands and file roles are in
the tutorial. `failure_lab.py` contains four explicitly invoked failure exercises. `native_lab.py`
is optional and requires xspcomm. Neither is automatically collected by the directory command above.

## Triggers

[basic_execution.py](https://github.com/XS-MLVP/toffee/blob/main/examples/triggers/basic_execution.py)
demonstrates Execution, RisingEdge, FallingEdge, Value, and `@xtrigger`.

```bash
PYTHONPATH=src python3 examples/triggers/basic_execution.py
```

## Sampling and persistent subscriptions

[subscription.py](https://github.com/XS-MLVP/toffee/blob/main/examples/triggers/subscription.py)
captures immutable data at sampling time before processing it in an async handler:

```bash
python3 examples/triggers/subscription.py
```

## Transaction checking

[scoreboard.py](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/scoreboard.py)
shows protocol-independent Drivers/Monitors, fixed-latency responses, and budgeted finish:

```bash
python3 examples/transactions/scoreboard.py
```

Expect `checked=8, passed=8, completed=8`. CI runs these introductory, native, subscription, and
transaction examples.

## Pipelined input, overlapping stages, and reordered responses

The [complete example guide](guides/pipeline-examples.md) compares continuous issue, serial and
overlapping input stages, nonblocking acceptance-only submissions, sequential send, and
FIFO/fixed-latency/key association. It also provides four reproducible failure scenarios.

```bash
python3 examples/transactions/pipeline.py
python3 examples/transactions/pipeline.py --backend native
python3 -m pytest -q tests/integration/test_pipeline_examples.py --require-xspcomm
```

The default command needs neither RTL nor native bindings; the second needs xspcomm. CI runs
successful scenarios on both Backends and uses pytest to check expected failures, sampled timing,
and cleanup.

## Agents and reference models

See [Agents and reference models](guides/agents-and-reference-models.md) for a stateful model,
Agent, and out-of-order response demonstration:

```bash
python3 -m examples.transactions.reference_model
python3 -m examples.transactions.reference_model --backend native
```

The [component and artifact recipe](guides/verification-flow.md) connects Driver → Monitor →
Scoreboard → coverage → pytest artifacts, including saving on failure and isolated seeds:

```bash
python3 -m pytest -q -s examples/transactions/test_pipeline_coverage.py
```

## Functional coverage

[functional_coverage.py](https://github.com/XS-MLVP/toffee/blob/main/examples/coverage/functional_coverage.py)
demonstrates coverpoints, bins, crosses, JSON output, and coverage checking.

```bash
python3 examples/coverage/functional_coverage.py --output output/functional-coverage.json
```

Continue with:

- [Class declarations](guides/coverage-classes.md): reusable points/models, manual sampling, and signal binding.
- [Transaction coverage](guides/coverage-transactions.md): latency and completion order by request ID.
- [Temporal coverage](guides/coverage-patterns.md): Sequence and FSM completions.

## Move from teaching substitutes to real DUTs

After the tutorial, choose the environment relevant to your goal; there is no need to run every
example first. Real DUTs require Picker, Verilator, and native bindings, and are not part of default
tutorial collection.

| Goal | Entry point | Framework features |
| --- | --- | --- |
| Expand inputs into downstream commands; check routing, addresses, data, and errors | [e203 build and use](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/e203/README.md) | Bundle snapshots, acceptance-time model updates, Scoreboard context, coverage, pytest results, LCOV/HTML, failure artifacts |
| Verify refill, masks, replacement, MMIO, and probe/release | [Cache build and use](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/cache/README.md) | Project SyncDriver, Monitor, multi-beat CoherenceAgent, Scoreboard, reference memory, coverage, background cleanup |
| Verify submit, pipelines, reordering, cancellation, and deadlines | [Pipeline scenarios](guides/pipeline-examples.md), [Agents/models](guides/agents-and-reference-models.md) | AsyncDriver, single-cycle templates, Agent, FIFO/key/fixed latency, finish, memory/native Backends |

e203 and Cache READMEs walk through building, directed cases, additional seeds, and reports.
Choose Drivers to match DUT timing instead of changing the protocol to showcase an API.
Cache's independent overlap probe is still expected to fail; a passing functional campaign does
not establish that this known issue is fixed.
