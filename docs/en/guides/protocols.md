# Optional protocol views

Generic Drivers, Monitors, and Agents do not require ready/valid. Use these helpers only for interfaces
that implement that protocol; other interfaces define acceptance in their own components.

## ReadyValid

```python
from toffee import Bundle, ReadyValid, Role

producer = ReadyValid(
    clock=dut.clock,
    valid=dut.valid,
    ready=dut.ready,
    bits=Bundle(data=dut.data),
    role=Role.PRODUCER,
    name="request",
)
```

Roles are from the testbench's perspective:

- PRODUCER drives valid/bits and observes ready.
- CONSUMER drives ready and observes valid/bits.
- MONITOR only observes.

ReadyValid.fire is a compiled Trigger sampled at DriveStable and supports persistent subscriptions.

## Capture and drive

ReadyValidMonitor captures an immutable payload at a fire candidate and delivers Transfer after confirmation
at the corresponding rising edge. Its decoder receives a snapshot, not live pins.
This differs from SamplingMonitor's direct capture at its supplied Trigger.
See [Monitor](monitors.md) for lifecycle, delivery policy, and errors.
Use drive_ready_valid inside a project SyncDriver or AsyncDriver subclass:

```python
async def _drive_one(self, request):
    return await drive_ready_valid(
        self.port.clock, self.port.valid, self.port.ready,
        lambda: self.port.bits.drive(request),
        bits=self.port.bits,
    )
```

The subclass still declares its driven signals. The helper drives payload/valid at falling-stable,
waits for ready at DriveStable after same-phase writes settle, and deasserts valid after acceptance.
The drive callback must synchronously return None. `bits=` commits Picker Rise-mode payload before sampling,
including when ready depends on that payload. The helper also commits/deasserts Rise-mode valid.
There is no built-in ReadyValidDriver and no implicit ready/valid timing in generic Drivers.

Optional `admit=lambda: bool(...)` runs in the actual drive slot before encoding.
False performs no encoding or payload/valid write and retries next slot.
True is followed immediately by drive without an intervening await.
The callback must synchronously return bool; errors propagate to the send task.
Admission controls starting a request, not ready: once started, payload stays held until protocol acceptance.
The caller owns wait budgets and cancellation.

Decoupled aliases ReadyValid. flipped() creates an opposite-role view without copying or reconnecting signals.
A role/direction mismatch fails during binding or acquisition of drive ownership.
