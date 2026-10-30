# Count events across cycles

Ordinary bins inspect one sample. Temporal bins recognize events over time, such as a response
within four cycles of acceptance or a condition held for two samples. Reuse `@xtrigger` for these
patterns. If Sequence and FSM are new to you, start with [compiled conditions and timing](compiled-triggers.md).

## A request/response example

This definition comes from the [complete example](https://github.com/XS-MLVP/toffee/blob/main/examples/coverage/trigger_bins.py).
`ProtocolBundle` groups already-bound request, response, and clock signals; it is not a new signal wrapper.

```python
from toffee import RisingEdge, Sequence, Wait, Within, xtrigger
from toffee.declarative import Bin, SignalCoverGroup, TemporalCoverPoint, covergroup
from toffee.ir import signal_expr
from examples.coverage.declarative_protocol import ProtocolBundle


@xtrigger(sample=RisingEdge("clock"))
def roundtrip(pins: ProtocolBundle, *, tag: int = 1, maximum: int = 4):
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == tag)),
        Within(1, maximum,
               signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
               & (signal_expr(pins.response.tag) == tag)),
    )


class Completions(TemporalCoverPoint[ProtocolBundle]):
    tag_one = roundtrip
    tag_one_fast = Bin.pattern(roundtrip.with_args(maximum=2))


@covergroup(schema_id="protocol.completions", contract="roundtrip/v1")
class ProtocolCoverage(SignalCoverGroup[ProtocolBundle]):
    completions = Completions()
```

`tag_one` uses the default four-cycle window; `tag_one_fast` uses two cycles. A response arriving
three cycles later hits only the former. Counts use the declared class attributes, for example
`coverage.completions.count(Completions.tag_one)`.

After instantiation, bind the project's pins and clock with
`bind(pins=..., sample=RisingEdge(clock), strategy="native")`, then give `coverage.runtime` to
Execution. For attributes calculated by a Monitor, such as latency or IDs, see
[transaction coverage](coverage-transactions.md).

## Parameters and terminal states

`roundtrip(pins, maximum=3)` creates an awaitable Trigger.
`roundtrip.with_args(maximum=3)` stores arguments for later signal binding. Required arguments
must be supplied this way when the definition is used before binding.

An FSM may have terminal states such as `OK` and `BAD_DATA`. Select outcomes with
`Bin.pattern(definition, terminals=("OK",))`, and wrap erroneous outcomes in `Bin.illegal(...)`.
Reaching an unselected terminal still ends the match without incrementing that bin.

Within one coverpoint, terminal bins using the same process can share execution state while
keeping separate counts. Different windows or parameters execute separately.
`compile().explain()` shows this sharing.

## Track overlapping requests

The default tracks one active match. Enable concurrency with explicit capacity:

```python
Bin.pattern(roundtrip, overlap=True, max_active=8)
```

Three completions in one cycle count three times. Exhausting capacity raises an error and marks
collection incomplete instead of silently dropping events. This does not associate arbitrary
dynamic request IDs with their responses.

You cannot directly cross `TemporalCoverPoint` instances because their processes may finish at
different times. Form a completed transaction in the Monitor and cross its fields instead.

## Execution and shutdown

`strategy="native"` requires native support and fails if capabilities are insufficient.
`"auto"` allows Python fallback, recording the strategy and reason. See
[expressions and resource management](expressions-and-resources.md) for native requirements and types.

Cancelling an ordinary await does not clear the covergroup's own matching history. Exit its
Execution to stop sampling; use the group's history operations to clear history explicitly.

## Run and interpret the results

```bash
python3 -m examples.coverage.trigger_bins --engine memory --strategy python
python3 -m examples.coverage.trigger_bins --engine xcomm --strategy native
```

The example sends IDs 1, 2, 3 and receives responses in order 2, 1, 3.

| Coverage item | Count | Reason |
| --- | ---: | --- |
| `requests.handshake` | 3 | Three requests accepted |
| `completions.tag_one` | 1 | ID 1 returned within four cycles |
| `completions.tag_one_fast` | 0 | ID 1 missed the two-cycle window |
| `completions.tag_two_ready` | 1 | Ready stayed high for two samples after ID 2's response |
| `completions.correct_data` | 1 | ID 1 returned correct data |
| `completions.bad_data` | 0 | No bad data observed |

An uncovered item may simply mean its scenario did not occur in this run. Assertions and
checkers still determine whether the design behaves correctly.
