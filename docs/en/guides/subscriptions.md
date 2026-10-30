# Subscribe persistently and capture snapshots

## Declare an on subscription

```python
import asyncio

from toffee import Execution, RisingEdge, on

snapshots = asyncio.Queue()

# clock 与 payload 是项目已经绑定的时钟和 Bundle。
@on(RisingEdge(clock), capture=lambda event: (event.tick, payload.sample()))
async def observe(snapshot):
    snapshots.put_nowait(snapshot)

async with Execution(backend) as execution:
    execution.subscribe(observe.bind())
    tick, value = await snapshots.get()
    # 消费命中 phase 保存的不可变 BundleValue。
    assert value == expected
```

`await trigger` creates a one-shot registration. `@on` uses the same compilation path but rearms after matches
and delivers results through a handler queue.

- `lossless` fails explicitly when its queue fills.
- `latest` drops old queued items to retain recent values.
- `capture=` must synchronously create an immutable snapshot at the matching phase.
- The handler must be `async def`.

A handler receives the captured value, or XEvent when capture is omitted. Handlers can run later;
rereading a live signal does not recover the original sample.
The example's trigger is already bound, so `.bind()` takes no arguments.
A subscription declared against a trigger factory, such as `@on(handshake)`, instead uses `.bind(dut)`.
Execution cancels and reclaims subscriptions on exit.

Run the pure-Python example:

```bash
python3 examples/triggers/subscription.py
```

## Choose a subscription or Monitor

Use a [Monitor](monitors.md) when tests need `await recv()` or response checking.
Use `@on` for component handlers that persistently process samples or state events, without blocking the event loop.
Capture occurs at the match phase even if the handler is slow. A lossless overflow raises
SubscriptionOverflowError; capture or handler exceptions fail Execution, which still reclaims tasks.

The decorator returns XSubscriptionSpec; bind returns BoundSubscriptionSpec; Execution.subscribe returns
an active Subscription. Components can cancel it early with `execution.reactor.cancel_subscription(subscription)`.
Ordinary tests rely on Execution cleanup.
Value modes remain enter/each_sample/change: persistent rearming does not convert enter into a notification every high cycle.
