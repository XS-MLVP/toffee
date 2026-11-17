# Optional protocol views

Generic Drivers, Monitors, and Agents use project-defined acceptance rules.
Use these helpers for interfaces that implement the ready/valid protocol.

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
at the corresponding rising edge. Its decoder uses the saved snapshot.
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
Projects explicitly select this helper to apply ready/valid timing.

Optional `admit=lambda: bool(...)` runs in the actual drive slot before encoding.
False retains the current signals and retries next slot.
True is followed immediately by the synchronous drive operation.
The callback must synchronously return bool; errors propagate to the send task.
Admission controls starting a request; ready controls acceptance. Once started, payload stays held until acceptance.
The caller owns wait budgets and cancellation.

Decoupled aliases ReadyValid. flipped() creates an opposite-role view over the existing signals.
A role/direction mismatch fails during binding or acquisition of drive ownership.
