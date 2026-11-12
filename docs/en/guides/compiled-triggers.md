# Compile conditions, sequences, and state machines

## Declare an xtrigger

`@xtrigger` compiles a condition into an engine expression. With XCommClockBackend, the C++ engine
evaluates supported conditions without Python polling each cycle:

```python
from toffee import RisingEdge, xtrigger


@xtrigger(sample=RisingEdge("clk"))
def handshake(dut):
    return dut.valid & dut.ready & ~dut.flush


event = await handshake(dut)
```

Use `&`, `|`, and `~` for Boolean composition, not Python `and`, `or`, or `not`.
Signals, unsigned constants, and comparisons are supported; widths are checked when armed or compiled.
For arithmetic and signed values use explicit conversions and [expression rules](expressions-and-resources.md).
Data bitwise operations use `bit_and/or/xor/not()`; logical operators retain their condition-composition meaning.
The decorator declares a specification; `handshake(dut)` binds it. `sample` can be a phase Trigger or a DUT attribute-path string.

### Constant array indices

```python
@xtrigger(sample=RisingEdge("clk"))
def selected_word(dut):
    return dut.matrix[3][-1] == 42
```

Integer indices resolve to the original `XData` at binding. The native Backend
then evaluates the leaf expression without invoking Python array selection on
every cycle. Negative indices use the view's actual bounds. Attribute/index paths round-trip
through portable IR; attribute-only paths keep their existing syntax.
`Value(dut.matrix[3][-1], 42)` can also use a leaf directly. Dynamic indices such
as `dut.matrix[dut.index]`, slices, implicit packed bit selection, and whole-array
comparisons are unsupported. String sources keep their existing attribute-path
syntax; explicit Field selection uses `Field(("matrix", 3, -1))`, not a
bracket-string parser.

## Declare a Python predicate

```python
from toffee import pytrigger

allowed_values = {1, 2, 3}

@pytrigger(sample=RisingEdge("clk"))
def model_accepts(dut):
    return dut.data.U() in allowed_values
```

`@pytrigger` calls a synchronous condition at its specified sample phase.
For asynchronous sources use QueueTrigger, AsyncioEventTrigger, or TaskComplete.

## Recognize a non-overlapping sequence

```python
from toffee import Hold, Next, Sequence, Wait, Within

@xtrigger(sample=RisingEdge("clk"))
def request_then_ack(dut):
    return Sequence(
        Wait(dut.req & ~dut.flush),
        Within(1, 8, dut.ack),
        Hold(dut.valid, cycles=2),
    )
```

- `Wait(expr)` waits for a condition.
- `Next(expr)` requires the immediately following sample; a miss restarts the sequence.
- `Within(min, max, expr)` waits within an inclusive cycle window.
- `Hold(expr, cycles=N)` requires N consecutive matching samples.
- Each registration has one active match state; Sequence matching is non-overlapping.

A Within timeout restarts the sequence. A Hold miss clears that step's consecutive count and keeps waiting
at the same Hold. Neither automatically raises an assertion: only a complete match emits an event.
Use an explicit [timeout](triggers.md) when a test requires a failure deadline. Stimulus sequencing can remain ordinary async code.

## Use an explicit FSM

```python
from toffee import FSM, State

@xtrigger(sample=RisingEdge("clk"))
def request_fsm(dut):
    return FSM(
        start="WAIT_REQ",
        states={
            "WAIT_REQ": State().when(dut.req).goto("WAIT_ACK"),
            "WAIT_ACK": State()
                .when(dut.flush).goto("WAIT_REQ")
                .when(dut.ack).trigger("ACK"),
        },
    )
```

Branches in a state are evaluated in declaration order. A terminal emits FsmEvent with its name in `event.terminal_state`.
For continuous delivery see [subscriptions](subscriptions.md); for transaction records see [Monitors](monitors.md).

Within includes both bounds; the first sample after entering the step has age 1. A sample completes at most one step.
The regressions cover early and upper-bound matches, restarts, and interrupted Hold runs:

```bash
python3 -m pytest -q tests/integration/test_compiled_patterns.py --require-xspcomm
```

The same sample tables check match ticks on MemoryBackend and the native engine.
Conditions can also feed [bound coverage](coverage.md), counted continuously in C++.
Coverage registrations have independent match history and do not change ordinary await semantics.
