# Turn requests into input timing with Drivers

Tests express operations; Drivers implement signal writes and protocol timing and report the actual acceptance event.

## Separate submission style from input duration

Sync Drivers drive while the caller awaits send. Async Drivers submit immediately and return XTransfer handles.
Independently, an input can occupy one cycle or several fixed stages. Input duration is not response latency.

| Submission | One-cycle input | Fixed multistage input |
| --- | --- | --- |
| Wait for acceptance | SyncSingleCycleDriver | SyncMultiCycleDriver |
| Submit now, await later | AsyncSingleCycleDriver | AsyncMultiCycleDriver |

Sync does not block the whole event loop; Async is not determined by whether a function uses `async def`.
For dynamic timing, subclass SyncDriver or AsyncDriver and define handshake and acceptance in `_drive_one()`.

## send and submit

| Operation | SyncDriver | AsyncDriver |
| --- | --- | --- |
| `driver.send(request)` | Returns a coroutine that has not run | Submits immediately; returns an input handle |
| `await driver.send(request)` | Drives and waits for acceptance | Submits and waits for acceptance |
| `driver.submit(request)` | Unsupported | Submits a transaction whose handle awaits a response |

send completion means acceptance, not output arrival. A response checker completes submit handles, normally through an Agent.
After complete acceptance, a transaction enters PROCESSING. `cancel()` only withdraws unaccepted work.
Closing with accepted but unfinished transactions raises DriverIncompleteError.

## Overlap input stages

`max_active=1` serializes complete input operations. Larger values allow requests to occupy distinct stages,
not to write the same signals simultaneously.
DriveStage(name, bits, idle, encode, cycles) describes each stage; stage signals must be disjoint.
Acceptance occurs on the last cycle of the last stage.
For backpressure or shared ports, use `async with self.resource_lock(name)` in a custom Driver.
Locks with the same name serialize use within that Driver; they do not infer hardware arbitration.
Input overlap and outstanding responses need separate configuration.

## Restore input state on close

SignalDriver enforces exclusive signal ownership, direction checks, and idle restoration.
Close active async Drivers with `await driver.aclose()` or an async context:

```python
from toffee import AsyncSingleCycleDriver, Bundle, Execution

async with Execution(backend):
    async with AsyncSingleCycleDriver(
        dut.clock, Bundle(data=dut.data), idle={"data": 0},
    ) as driver:
        inputs = [driver.send({"data": value}) for value in (1, 2, 3)]
        accepted = [await item for item in inputs]
```

This submits three inputs before waiting for acceptance, without waiting for responses.
Project tests normally use an [Agent](agents-and-reference-models.md); see [pipeline examples](pipeline-examples.md).

## Implement multicycle input

Use fixed-stage templates when the timing is known:

```python
from toffee import AsyncMultiCycleDriver, DriveStage

driver = AsyncMultiCycleDriver(
    clock,
    (
        DriveStage("address", address, 0, lambda request: request.address),
        DriveStage("payload", payload, 0, lambda request: request.payload, cycles=2),
    ),
    max_active=2,  # 两笔请求可以占用不同阶段；同一阶段始终互斥
)
```

SyncMultiCycleDriver accepts the same stages and waits in send.
AsyncMultiCycleDriver.send completes at acceptance; submit delegates response completion to a Monitor/Scoreboard.
Templates drive initial idle and restore the current stage's idle on cancellation or failure.
`max_active` limits concurrent input-driving operations, not accepted requests awaiting output.

For dynamic stages, define the actual timing; the zero idle values below belong only to this example:

```python
from toffee import ClockCycles, SyncDriver

class ThreeCycleInput(SyncDriver[int]):
    def __init__(self, clock, data):
        self.clock, self.data = clock, data
        super().__init__((data,), name="three-cycle")

    async def _drive_one(self, value):
        self.data.Set(value)
        try:
            return await ClockCycles(self.clock, 3)
        finally:
            self.data.Set(0)
```

`_drive_one()` must return the actual acceptance XEvent, not a response or None.
For background dynamic handshakes, subclass AsyncDriver and declare capacity/max_active;
there is no need for a separate pipeline base class. Protect shared signals with resource_lock.

## SingleCycle options

Both templates accept a clock, scalar/Bundle/packed/sequence data, idle, and optional synchronous encoder.
The encoder returns a value matching the driven shape. SyncSingleCycleDriver restores its explicit idle after driving or cancellation.
AsyncSingleCycleDriver additionally accepts `idle(last_request)`: None at startup, then the accepted request after each acceptance.
The callable must handle both. Close releases resources without replacing the already computed idle.
To restore initial idle, wait for acceptance and call `driver.quiesce()` before exit; callable idle receives None again.

```python
driver = AsyncSingleCycleDriver(
    clock, data, idle=lambda previous: 0 if previous is None else previous,
    encoder=lambda request: request, capacity=8,
)
```

Holding the previous input is valid only if the protocol permits it.
Use an [Agent](agents-and-reference-models.md) or [Scoreboard](scoreboard.md) to own response completion for submit transactions.
