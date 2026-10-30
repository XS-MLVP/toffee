# Sample and observe with Monitors

A Monitor captures signals at a specified event and stores the result for later checking, including outputs
that exist for only one cycle. Start with SamplingMonitor and define validity from the design.
Each record is `Transfer(event=event, value=snapshot)`, an observation rather than the XTransfer completion handle returned by submission.

## Capture and close

SamplingMonitor synchronously calls `capture(event)` at the explicit Trigger match and retains its tick/phase.
Return Bundle.sample() or another immutable snapshot, not a mutable DUT. Capture cannot be async or return an awaitable.

```python
from toffee import Bundle, Execution, RisingEdge, SamplingMonitor

outputs = Bundle(data=dut.data)
monitor = SamplingMonitor(
    RisingEdge(dut.clock), capture=lambda event: outputs.sample(), capacity=64,
)
async with Execution(backend) as execution:
    async with monitor.start(execution):
        first = await monitor.recv()
        second = await monitor.recv()
```

Sampling every cycle does not mean every cycle has a valid response. Supply a validity Trigger, or for a
fixed-latency datapath use `Scoreboard.bind(latency_cycles=..., sampled=True)` to select target windows.
SamplingMonitor does not infer acceptance; ReadyValidMonitor has its own candidate/acceptance rules.
See [examples](../examples.md) for fixed-latency checking.

Both built-in Monitors follow these rules:

- `aclose()` removes subscriptions, watchers, and tasks and discards unread buffers; it does not drain.
- Pending recv callers wake with MonitorClosedError on normal close; async iteration ends normally.
- Capture, decoder, or delivery errors retain their original exception. Once observed through recv, the same error is not repeated on exit. Unobserved errors surface at close; independent cleanup errors are retained.
- Closed instances cannot restart. Multiple batches share one active lifecycle.
- Cancelling one recv waiter does not close the Monitor. Context cancellation cleans the component without cancelling the host task.

By default, a bound Scoreboard owns Monitor startup, consumption, and close. Do not start or consume it separately.
An [Agent](agents-and-reference-models.md) wires this ownership automatically.
Call bounded finish before cleanup: discarding buffers is not strict completion checking.

## Choose delivery policy

| Use | Policy | At capacity |
| --- | --- | --- |
| Transaction checking or per-item coverage | `delivery="lossless"` (default) | Raise MonitorOverflowError |
| UI/telemetry needing only recent values | `delivery="latest"` | Drop oldest records; capacity=1 retains only the latest |

Monitors are queues, not broadcasts: recv callers compete for records.
When coverage and checking use one observation, the single consumer samples coverage before checking;
see [verification flow](verification-flow.md).

## Use a passive Agent

```python
monitor = SamplingMonitor(
    RisingEdge(clock), capture=lambda event: output.sample(),
    delivery="latest", capacity=1,
)
agent = Agent("telemetry", monitors={"output": monitor})
async with Execution(backend, agents=[agent]):
    observation = await agent.recv("output")
    snapshot, tick = observation.value, observation.event.tick
```

A known validity rule can use
`PythonPredicateTrigger("output-present", present, sample=RisingEdge(clock), mode="each_sample")`.
A cycle sample and a valid response are different contracts.
If response checking owns the stream, consume transaction results instead of calling recv on that stream.
For protocol-specific candidate sampling, see [protocols](protocols.md).
Custom Monitors implement start/recv/aclose or reuse SamplingMonitor's synchronous capture template.
