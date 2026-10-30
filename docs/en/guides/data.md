# Signals, Bundles, and packed data

## XData

DUT signals use XData. Inspect the underlying object with:

```python
from toffee import as_xdata

signal = as_xdata(dut.data)
```

Binding, width checks, reads, writes, and native conditions all use the same XData identity.

## Bundle

Group signals for named access, sampling, and driving:

```python
from toffee import Bundle

payload = Bundle(
    opcode=dut.req_opcode,
    address=dut.req_address,
    data=Bundle(lo=dut.req_data_lo, hi=dut.req_data_hi),
)

snapshot = payload.sample()
payload.drive({
    "opcode": 1,
    "address": 0x8000,
    "data": {"lo": 0x1122, "hi": 0x3344},
})
```

`sample()` returns immutable BundleValue snapshots for checking or coverage.
Leaves are LogicValue objects retaining width and X/Z. Convert explicitly when a model needs an integer,
for example `snapshot.opcode.as_int()`. Unknown bits raise instead of silently becoming valid data.
The live `payload` can change; pass `snapshot` into delayed processing rather than rereading pins later.

## PackedArray and PackedLayout

An equal-width one-dimensional bus:

```python
from toffee import PackedArray

lanes = PackedArray(dut.lanes, element_width=32, count=4)
lanes[2].Set(0x1234)
```

Nested and multidimensional layouts:

```python
from toffee import PackedLayout, PackedView

entry = PackedLayout.struct({
    "valid": PackedLayout.bits(1),
    "tag": PackedLayout.bits(7),
    "payload": PackedLayout.array(2, PackedLayout.bits(32)),
})
entries = PackedView(dut.entries_bus, PackedLayout.array(4, entry))

entries[0].drive({
    "valid": 1,
    "tag": 3,
    "payload": [0x11223344, 0x55667788],
})
```

Views access underlying signals directly. They neither copy simulator storage nor choose sampling time.

## Bind names or Picker metadata

Declare sources and expected widths with Field to catch interface changes during binding:

```python
from toffee import Bundle, Field

request = Bundle.bind(dut, {
    "address": Field("req_addr", width=32),
    "payload": {"data": Field("req_data", width=64)},
})
address_only = request.view_as({"addr": Field("address", width=32)})
assert address_only.addr is dut.req_addr
```

`Bundle.bind_tree(dut, signal_tree, source_prefix="io")` accepts explicitly supplied Picker metadata,
preserves signal identity, and checks widths. Keywords such as `in` become `in_`; consecutive numeric indices become sequences.
Names do not imply a protocol. Components can enumerate `fields` or `leaves()`.
`Bundle.drive()` requires a complete shape without extra fields; omitted fields are not implicitly held or zeroed.

## Bit ordering, padding, and four-state values

`lsb_first=True` places the first field or element at the low bits; False places it at the high bits.
Each array or struct sets its own ordering. Use stride, structure width, and explicit offsets for gaps:

```python
layout = PackedLayout.struct({
    "opcode": PackedLayout.bits(4, offset=0),
    "payload": PackedLayout.array(2, PackedLayout.bits(8), stride=16, offset=8),
}, width=40)
packet = PackedView(dut.packet, layout)
packet.payload[1].Set(0xA5)
```

Do not mix automatic and explicit offsets within one struct. Overlap is rejected unless intentional aliasing
uses `allow_overlap=True`. Driving writes leaves and preserves padding.
`split_packed()` constructs an equal-width PackedArray. These views require native XData sub-signal support.

`LogicValue(value, x_mask, width)` retains aval/bval. Check `is_known` before strict integer conversion.
`as_int(strict=False)` is for diagnostics that explicitly tolerate unknown bits, not a hardware truth value.
`BundleValue.as_dict()` returns a structure of snapshot leaves unaffected by later DUT changes.
Driver ownership checks underlying identity; another view cannot bypass conflicts.
See [Drivers](drivers.md) and [Monitors](monitors.md).
