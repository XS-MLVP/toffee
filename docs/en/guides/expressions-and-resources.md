# Native expressions and resource management

XCommClockBackend evaluates supported conditions in xcomm. For installation see
[native integration](../getting-started/native-and-next.md); required commit, package, ABI, and protocol versions
are in the [dependency manifest](https://github.com/XS-MLVP/toffee/blob/main/.github/native-dependencies.json).

## Separate conditions from data operations

In a Trigger, `&`, `|`, and `~` combine Boolean conditions. For operations on data bits use
`bit_and()`, `bit_or()`, `bit_xor()`, and `bit_not()`. Do not substitute Python and/or/not.

```python
from toffee import bits
from toffee.ir import signal_expr

data = signal_expr(pins.data)
negative = data.as_signed() < 0
low_byte = data.bit_and(255)
extended = data.as_signed().resize(129)
wrapped = (bits(255, 8) + 1) == 0
```

## Width and signedness

Python integers do not automatically overflow; native bit vectors have explicit widths.

| Operation | Semantics |
| --- | --- |
| `bits(value, width, signed=...)` | Declare width and signedness; reject constants outside the range |
| `as_signed()`, `as_unsigned()` | Reinterpret unchanged bits; signed 8-bit `0xff` is -1 |
| `resize(width)` | Extend according to original signedness, or truncate high bits |
| `resize(width, signed=...)` | Extend/truncate first, then apply target interpretation |
| `+`, `-`, `*`, unary minus | Truncate to result width; unsigned 8-bit 255 + 1 is 0 |
| `//`, `%` | Signed division truncates toward zero; remainder follows dividend sign; reject division by zero |
| `<<`, `>>` | Signed right shift extends the sign; reject negative shift counts |
| Comparisons | Typed operands need equal width and signedness; result is Boolean |

Plain integer constants are checked against the other operand's type.
`data.as_signed() == -1` is meaningful; unsigned data cannot directly compare with -1.
Convert mismatched types explicitly rather than relying on implicit promotion.

## Unknown values and support

Arithmetic uses two-state bit vectors, not full SystemVerilog four-state propagation.
Do not use it as an X/Z propagation model. XData/LogicValue four-state access is a separate capability;
see [signals and Bundles](data.md).
Strict native execution rejects unsupported expressions. Coverage models permitting automatic fallback record the reason in reports.

## Reclaim compiled programs

The backend caches up to 256 recent programs by default. Configure
`XCommClockBackend(clock, program_cache_capacity=256)`; zero disables caching.
The capacity is neither an active Trigger limit nor a fixed expression-node limit.

Cache eviction does not invalidate active waits or coverage registrations.
The backend reclaims unused expressions at safe points; `backend.collect_programs()` requests collection
without clearing active Sequence/FSM histories or coverage counters.
Execution releases its registrations but does not close the backend. Close the backend in its owner's `finally`,
and retain DUT signals until no programs reference them.
