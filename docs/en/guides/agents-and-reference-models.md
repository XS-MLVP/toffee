# Agents and reference models

An Agent groups an interface's Driver, Monitors, and checking configuration.
The environment constructs it, connects a model, and registers it with Execution.
Tests send requests and check results without rewiring components:

```python
model = AccumulatorModel()
agent = AccumulatorAgent(dut, response_timeout_cycles=5)
agent.connect(model)

async with Execution(dut.backend, agents=[agent]):
    transfers = [agent.submit(command) for command in commands]
    await agent.finish(timeout_cycles=30, observe_cycles=1)
    results = [await transfer for transfer in transfers]
```

An environment or pytest fixture can own the single Execution context and expose started Agent instances.
`connect()` only wires objects: it neither starts simulation nor advances model state.

## Responsibilities

| Layer | Responsibility |
| --- | --- |
| Agent | Interface components, transaction operations, model connection, response configuration |
| Driver | Input timing and explicit acceptance events |
| Monitor | Immutable observations at specified sample events |
| Reference model | Functional state transitions and expected responses for accepted requests |
| Scoreboard | Expectations, association, comparison, timeouts, and residual checks |
| Execution | Registered Agent startup, checks, and resource cleanup |
| Project environment/fixture | DUT, model, initialization, seeds, configuration, and artifacts |

Models are independently owned. An Agent does not create, close, or reset them; multiple Agents may share one.
Scoreboard remains independently usable for direct checks or special association.

## Define an Agent

The runnable [AccumulatorAgent](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/reference_model.py)
encapsulates its input, sampling, and association rules:

```python
class AccumulatorAgent(Agent[Input]):
    def __init__(self, dut, *, response_timeout_cycles):
        monitor = SamplingMonitor(
            PythonPredicateTrigger(
                "response-present", lambda: dut.output is not None,
                sample=RisingEdge(dut.clock), mode="each_sample",
            ),
            capture=lambda event: dut.output,
        )
        super().__init__(
            "accumulator", driver=Input(dut),
            monitors={"response": monitor}, response_monitor="response",
            clock=dut.clock, response_timeout_cycles=response_timeout_cycles,
            request_key=lambda request: request.tag,
            response_key=lambda response: response.tag,
        )
```

Response presence here is this DUT's rule. The framework does not infer ready/valid, reset/flush, acceptance, or latency.
Without key functions, association is FIFO. `latency_cycles` and `sampled` follow Scoreboard semantics.
`compare` customizes comparison; `capacity` limits active transactions and unmatched observations.

## send, submit, and Driver type

| Configuration | send | submit |
| --- | --- | --- |
| SyncDriver, no response checking | Drive in caller; return acceptance XEvent | Unsupported |
| SyncDriver with checking | Drive in caller; checker follows the accepted request | Unsupported |
| AsyncDriver, no checking | Submit immediately; return original acceptance handle | Unsupported without response checking |
| AsyncDriver with checking | Submit immediately; return acceptance view; checking continues | Return XTransfer for checked response |
| Passive Agent without Driver | Unsupported | Unsupported |

SingleCycle variants inherit their corresponding behavior. Agent does not convert a SyncDriver into a background Driver.
Async driving continues to use its original worker.
To wait only for acceptance:

```python
event = await agent.send(command)
await agent.finish(timeout_cycles=20)  # 已配置响应检查时，仍须等响应并检查
```

For responses or multiple outstanding transactions with an AsyncDriver:

```python
transfers = [agent.submit(command) for command in commands]
await agent.drain(timeout_cycles=30)
# drain 允许后续批次继续提交；Monitor 持续工作。
more = agent.submit(next_command)
await agent.finish(timeout_cycles=20)
result = await more
```

With checking enabled, send changes only the caller's wait endpoint. Its acceptance view is awaitable and
cancellable before acceptance, but does not promise every XTransfer field. Use submit for full transaction state.
Unawaited response failures still reach finish or Execution exit.
Accepted inputs cannot be withdrawn. Cancelling an AsyncDriver acceptance/response waiter does not cancel its transaction.
Cancelling SyncDriver.send interrupts driving in the caller.
With response checking, Driver and checker capacity remain occupied until response completion.
For input-only interfaces, omit `response_monitor`.

## Advance models on acceptance

A model is an ordinary Python object:

```python
class AccumulatorModel:
    def __init__(self):
        self.total = 0

    def accept(self, command):
        self.total += command.operand
        return Result(command.tag, self.total)
```

The automatic path consumes Driver acceptance records: AsyncDriver's stream or SyncDriver's returned event.
In notification order it calls `ref.accept(request)`, stores the expected snapshot, and associates the response.

- Submission alone does not advance the model; cancelling an unaccepted request does not call it.
- Stateful models follow acceptance order, not submission or completion order.
- Each expected response must be an independent snapshot; keys associate reordered responses.
- Explicit `expected=` overrides this comparison value but **still advances the connected model**. It is also usable without a model.
- Model errors reach the transaction, finish, or Execution exit; already observed background errors are not repeated.

Model entry points must be synchronous; async methods and awaitable returns fail explicitly.
Compute from specification and valid input, not the DUT output being checked.
Remote asynchronous models require project-defined synchronization and budgets.
Standalone `Scoreboard(expected=...)` differs: that predictor runs only when explicit expected is absent.

## Lifecycle and exclusive consumption

Execution starts Agents in registration order and closes them in reverse.
An Agent starts Monitors, acquires Driver ownership, then starts response checking.
On exit it checks/stops Scoreboard, closes Driver and Monitors, then releases Execution resources and backend lease.
Partial startup failure cleans up already started components without starting later Agents.

finish closes admission, drains, observes for the requested window, and checks balances; it does not close the Agent or Monitor.
Execution exit checks and cleans up again without an unbounded drain.
Continued monitoring after the final response detects extra output.
Components must be unstarted and exclusively owned. Duplicate Agents or shared Driver/Monitor objects are rejected.
Closed Agents and built-in Monitors cannot restart. Drivers must also clean up failed internal initialization.

The response Monitor belongs to the internal Scoreboard; `agent.recv(response_name)` rejects a second consumer.
Other named Monitors support `await agent.recv(name)`. Do not bypass an active Agent to submit checked work or consume its response stream.
There is no public Agent bind, async context, or independent close operation.
Normal exit, failure, and cancellation clean up components while preserving original and independent cleanup errors.
External tasks remain with their owners; callers must finish or cancel concurrent SyncDriver.send tasks before environment exit.

## Passive and multi-interface models

Omit Driver for a passive Agent. Automatic connect(ref) requires active Driver acceptance events.
For passive bus checking, consume an input Monitor and explicitly invoke the model and Scoreboard.check;
do not also advance the same model from Driver notifications.
Sharing a model does not define cross-interface hardware order. Specify same-tick arbitration and state updates
in the project's input aggregation layer. Independent commutative operations can connect directly.
Reset, flush, retirement, and commit semantics also belong to the project.
The automatic response path is one accepted request to one response. Zero/multiple/autonomous outputs need an explicit association contract.
None is a valid expected value, not an absence-of-response marker.

## Run the example

```bash
python3 -m examples.transactions.reference_model
python3 -m examples.transactions.reference_model --backend native
```

Inputs 3, 5, 7 expect 3, 8, 15, with response tags 2, 1, 3.
DUT and model do not share state or prediction functions, and the model runs independently of Execution.
Regressions cover Sync/Async inputs, batches, cancellation, expected overrides, model errors, shared models, and cleanup.
See [verification flow](verification-flow.md) for coverage and artifacts.
