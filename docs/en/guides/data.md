# Signals, Bundles, and arrays

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

## Fixed unpacked arrays

Pass a Picker-generated array directly into a Bundle. Toffee adapts it to a
`SignalArray`: a bounded aggregate with a read-only structure that retains the
original `XData` leaf identity. For RTL declared as
`logic [64:0] matrix [3:2][-1:1]`:

```python
from toffee import Bundle

payload = Bundle(matrix=dut.matrix)
assert payload.matrix.bounds == ((3, 2), (-1, 1))
assert payload.matrix.shape == (2, 3)
assert list(payload.matrix.indices) == [3, 2]
payload.matrix[3][-1].Set(42)
snapshot = payload.sample()
assert snapshot.matrix[3][-1].as_int() == 42
```

Indices use the view's declared coordinates, including negative RTL bounds.
Iteration follows declaration order, last dimension fastest.
`SignalArray(source)` also explicitly adapts public views exposing
`bounds/shape/indices` and indexing, including xcomm `XArray`. The adapter uses
these public protocols and retains the source leaves. Elements stay lazy until
whole-array sampling or driver ownership enumerates the selected leaves.

Sampled arrays are immutable `ArrayValue` snapshots retaining bounds and
four-state `LogicValue` leaves. `compile_sampler()` caches structure and getters
and reads fresh values on each call. `ArrayValue.as_tuple()` discards bounds while
retaining four-state leaves; `as_dict()` returns nested mappings from actual integer
indices to plain integers, rejecting unknown bits. `BundleValue.as_dict()` uses
the same representation for arrays.

```python
payload.drive({
    "matrix": {
        3: {-1: 10, 0: 11, 1: 12},
        2: {-1: 20, 0: 21, 1: 22},
    },
})
saved = payload.sample()
payload.drive(saved)
payload.drive({"matrix": [[10, 11, 12], [20, 21, 22]]})
```

Drive an `ArrayValue` with identical bounds, mappings keyed by actual indices,
or nested lists/tuples in **declaration order**. The whole array's shape, indices,
and unknown values are checked before writing. Simulator IO failures follow the
backend's write semantics. Structure is read-only and its leaves remain live signals.
Element selection uses constant integer indices on fixed-size arrays.
Scoreboard checks snapshot bounds and reports differences using actual indices
such as `matrix[3][-1]`.

Port arrays retain RTL bounds. Native arrays use the same recursive `XArray`
interface; Picker's `NewXArray` for internal mem_direct arrays reports zero-based
storage coordinates:

```python
from toffee import SignalArray

memory = SignalArray(dut.xcfg.NewXArray(name))
memory[0][0].AsImmWrite()
memory[0][0].Set(42)
```

Adapters use the leaves exposed by the source view, retaining their backend,
write mode and registration. Generated ports keep their existing `XPort`/`XClock`
edge commits; callers choose immediate writes or explicit commits for dynamically
queried internal leaves. Flattened lists use positional sequence coordinates.

Native array views use xcomm's `XArray` factories (native ABI 6) and matching
Picker-generated bindings. The former separate native-array protocol is removed;
regenerate DUTs and migrate to recursive indexing after toolchain upgrades.

The DPI and mem_direct array paths have been verified on Verilator **5.026,
5.048, and 5.052**. See the runnable
[RTL array example](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/arrays/README.md).

## RTL structs and recursive arrays

With a matching Picker 3 / xcomm build (**native ABI 6**), pass
`XStruct` and `XArray` directly to existing Toffee components:

```python
from toffee import Bundle

payload = Bundle(
    record=dut.record_in,
    header=dut.packed_in.StructView(),
    matrix=dut.matrix.ArrayView(),
)
snapshot = payload.sample()
word = snapshot.record.entries[-1].payload[3].as_int()
payload.drive(snapshot)
```

`Bundle(xstruct)` adapts a struct root; arrays become `SignalArray`, including
arrays of structs. Snapshots recursively combine `BundleValue`, bounded
`ArrayValue`, and four-state `LogicValue`. Field names, declaration order, index
directions, and negative indices come from the source. Native fields colliding
with Bundle methods remain accessible through `bundle["sample"]`, for example;
ordinary user-authored Bundle mappings still reject reserved names.

Packed struct/array fields and packed elements in unpacked arrays default to
whole `XData` values. Pass the whole value to Bundle directly, or explicitly
request its `StructView()` / `ArrayView()` for structural access:

```python
header = Bundle(payload.record.entries[-1].header.StructView())
header.drive({"tag": 17, "valid": 1})
```

Explicit views reuse the Data's associated RTL type. Packed views share their
parent storage; unpacked fields keep independent leaves. The source SDK owns
layout, storage, write modes, clock callbacks and XPort registration. Reuse the same source view
when driver ownership must recognize a shared leaf.

`ReadyValid`, drivers, monitors, compiled sampling, native leaf conditions and
coverage use these same leaves. Whole-tree drives validate fields, array bounds,
X/Z drive values, output directions and read-only backends before the first write.
Four-state sampling is supported; driving a `LogicValue` containing X/Z still
raises rather than silently discarding unknown bits.

VPI sources returned by `dut.GetInternalSignal(name, use_vpi=True)` use the same
adapters. Explicitly request a packed value's `StructView()` / `ArrayView()` to
drive or sample structured transactions. Initialize the model before driving VPI inputs; Verilator's first
evaluation can overwrite values written before initialization.
Dynamically queried VPI inputs use caller-selected immediate writes or explicit
commits before simulation. Typed internal views use layouts exported by Picker.

The [aggregate RTL example](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/aggregates/README.md)
checks DPI, mem_direct and VPI on Verilator 5.052. Picker requires 5.048+ for
aggregate ports and 5.052+ for the verified VPI unpacked-struct path.

## PackedArray and PackedLayout

`PackedLayout.struct` remains a manually declared Toffee-side view over an
existing packed `XData` vector, independent of Picker's RTL `Layout` metadata.
For supported RTL structs and packed arrays, prefer the generated `XStruct` /
`XArray` views above rather than duplicating their layout by hand.

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

Views access underlying signals directly at the caller's chosen sampling time.

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
The caller selects the protocol. Components can enumerate `fields` or `leaves()`.
`Bundle.drive()` validates the exact complete field set before writing.
Port nodes with `Unpacked: [[left, right], ...]` bind to `SignalArray`, checking
dimension bounds and each element's width. Use
`Field(("matrix", 3, -1), width=65)` to select one leaf explicitly.
Port `Layout` metadata checks recursive struct/array fields, bounds and widths.
It accepts an object or JSON string and checks nesting depth and declaration consistency
against the existing leaves. Packed roots retain their original complete Data. Request
`StructView()` / `ArrayView()` for structural access.
When a port also prefixes other port names (`data` / `data_0`, `req` / `req_valid`),
retain that port and promote conflicting descendants to sibling fields with their
underscore-joined names. Duplicate resulting names produce a binding error.
Noncolliding consecutive numeric groups remain sequences. `ReadyValid.bind_tree`
also accepts an existing explicit `PackedView` or a complete packed Data payload
with `Layout` metadata.

For a matching Picker build that delivers one `.xscfg` configuration database,
read the port metadata directly from that database:

```python
import json
from toffee import Bundle

pins = Bundle.bind_tree(dut, json.loads(dut.xcfg.Metadata("ports")))
```

`bind_tree` resolves generated attributes. If Picker renamed a port to protect a
wrapper member or method, bind that port explicitly using `dut["original_name"]`
or its generated attribute. Binding uses the SDK's existing layouts and native leaves.

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

Choose either automatic or explicit offsets within one struct. Intentional aliasing
uses `allow_overlap=True`. Driving writes leaves and preserves padding.
`split_packed()` constructs an equal-width PackedArray. These views require native XData sub-signal support.

`LogicValue(value, x_mask, width)` retains aval/bval. Check `is_known` before strict integer conversion.
`as_int(strict=False)` is for diagnostics that explicitly tolerate unknown bits.
`BundleValue.as_dict()` returns a structure of snapshot leaves unaffected by later DUT changes.
Driver ownership checks the underlying `XData` identity across views.
Reuse one source view for the same element to retain that identity.
See [Drivers](drivers.md) and [Monitors](monitors.md).
