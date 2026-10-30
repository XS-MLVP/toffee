# Cover transaction latency and out-of-order completion

“Ready stayed low for two cycles” is signal timing. “Request 7 returned after three cycles” is
a transaction property: it requires retaining the request and associating its response by ID.
Keep these measurements distinct.

This page uses the [request/response example](https://github.com/XS-MLVP/toffee/blob/main/examples/coverage/declarative_protocol.py)
and the syntax introduced in [class declarations](coverage-classes.md).

## Observe consecutive samples

```python
from toffee.declarative import Bin, CoverPoint, OverlapPolicy, coverpoint


@coverpoint(overlap=OverlapPolicy.ALLOW)
class ReadyPoint(CoverPoint[bool]):
    stalled = Bin.values(False)
    accepted = Bin.values(True)
    waited_two_then_accepted = Bin.transition(False, False, True)
```

Three consecutive valid samples of `False, False, True` hit the final bin once. The first two also
hit `stalled`, and the last hits `accepted`, so overlap is explicitly allowed.

The example samples only valid requests. Invalid cycles break transition history; matches cannot
span idle gaps. This coverage does not establish that a request received a correct response.

## Sample when a multi-cycle condition completes

A group's sampling condition can be a Sequence instead of each clock edge:

```python
from toffee import RisingEdge, Sequence, Wait, Within, xtrigger
from toffee.ir import signal_expr


@xtrigger(sample=RisingEdge("clock"))
def tag_one_roundtrip(pins, *, maximum=4):
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == 1)),
        Within(1, maximum,
               signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
               & (signal_expr(pins.response.tag) == 1)),
    )
```

This waits for acceptance of request 1, then acceptance of its response within the next 1–4 cycles.
When used as `coverage.bind(trigger=...)`, fields are sampled at response completion. Other IDs
cannot complete this Sequence, and responses outside the window do not hit.

This matches a fixed ID; it does not remember arbitrary request IDs automatically. For bins with
their own Sequence or FSM, use [temporal coverpoints](coverage-patterns.md).

## Associate arbitrary request IDs

The example Monitor captures requests and responses at a stable phase. The Collector then:

1. Saves ID, opcode, and acceptance time at the request handshake.
2. Uses the response ID to find its original request.
3. Computes latency, checks data, and identifies out-of-order completion.
4. Produces one `CompletedTransaction` and samples it once.

`TransactionCoverage` bins opcode, latency, data correctness, and completion order, and can cross
opcode with latency. Association happens in Python, not automatically inside native bins.

A Monitor has one consumer. To check and collect coverage together, dispatch the same record
from that consumer; do not let two tasks compete for `recv()`. Save a snapshot when sampling,
rather than rereading pins later during asynchronous processing.

## Keep checking independent of coverage

A missing response produces no completed transaction and no latency sample. A hit in the correct-data
bin does not prove that all requests returned correct results. Checkers or Scoreboards must still
report missing responses, wrong data, unknown IDs, and duplicate responses.

Changing windows, sampling conditions, or field meanings prevents direct merging with old results.
Switching between Python and native execution alone does not change the meaning of the same data.

## Run the example

```bash
python3 -m examples.coverage.declarative_protocol --engine memory --strategy python
python3 -m examples.coverage.declarative_protocol --engine xcomm --strategy native \
  --output output/transactions
```

The example reports request, completion-time, and transaction coverage separately. Compare what
each counts before applying the structure to your project's Monitor and checkers.
