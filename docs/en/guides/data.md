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

Indices are the view's actual coordinates; negative indices are not positions
from the end. Iteration follows declaration order, last dimension fastest.
`SignalArray(source)` also explicitly adapts public views exposing
`bounds/shape/indices` and indexing, including xcomm `XArray`. It neither depends
on Picker's private Python classes nor copies or rebinds leaves. Constructing an
adapter does not read or expand all elements; whole-array sampling and driver
ownership enumerate the selected leaves when needed.

Sampled arrays are immutable `ArrayValue` snapshots retaining bounds and
four-state `LogicValue` leaves. `compile_sampler()` caches structure and getters,
not sampled values. `ArrayValue.as_tuple()` explicitly discards bounds while
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
and unknown values are checked before writing; simulator IO failures do not imply
transaction rollback. Structure is read-only: do not replace elements; the leaves
remain live signals. Slices, dynamic indexing, and dynamic arrays are unsupported.
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

Adapters do not compute element addresses, DPI indices, or backend-specific
handles; they use the leaves exposed by the source view. They do not change
write modes, advance time, or register additional `XPort` entries. Generated
ports keep their existing `XPort`/`XClock` edge commits; callers choose immediate
writes or explicit commits for dynamically queried internal leaves. A flattened
list remains a positional sequence; RTL bounds are not guessed.

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
parent storage; unpacked fields keep independent
leaves. Toffee does not compute offsets, copy aggregate storage, add clock
callbacks or XPort entries, or change write modes. Reuse the same source view
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
Dynamically queried VPI inputs are not committed by the generated
XPort: explicitly select immediate writes or arrange commits before simulation.
Do not infer internal packed-struct layouts that Picker did not export.

The [aggregate RTL example](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/aggregates/README.md)
checks DPI, mem_direct and VPI on Verilator 5.052. Picker requires 5.048+ for
aggregate ports and 5.052+ for the verified VPI unpacked-struct path. These results
do not establish aggregate runtime support on UVS, VCS or GSIM.

## PackedArray and PackedLayout

`PackedLayout.struct` remains a manually declared Toffee-side view over an
existing packed `XData` vector, independent of Picker's RTL `Layout` metadata.
It does not infer RTL types or add support for unsupported ports such as unions.
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
Port nodes with `Unpacked: [[left, right], ...]` bind to `SignalArray`, checking
dimension bounds and each element's width. Use
`Field(("matrix", 3, -1), width=65)` to select one leaf explicitly.
Port `Layout` metadata additionally binds recursive structs and arrays and checks
their fields, bounds and widths. Packed roots use xcomm's `StructView` or
`ArrayView` factory, not a guessed simulator layout. Metadata must be an
unambiguous tree: if a node is both a port leaf and a prefix of other port names,
bind the selected ports explicitly instead of accepting that ambiguous node.

For a matching Picker build that delivers one `.xscfg` configuration database,
read the port metadata explicitly; no separate `signals.json` is required:

```python
import json
from toffee import Bundle

pins = Bundle.bind_tree(dut, json.loads(dut.xcfg.Metadata("ports")))
```

`bind_tree` resolves generated attributes. If Picker renamed a port to protect a
wrapper member or method, bind that port explicitly using `dut["original_name"]`
or its generated attribute rather than assuming the original attribute is a pin.
This adapter does not reconstruct missing layouts or native addresses.

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
It identifies the same `XData`, not physical aliases across distinct `XData` objects;
reuse one source view for the same element rather than guessing identity from addresses.
See [Drivers](drivers.md) and [Monitors](monitors.md).
