# Picker metadata fixtures

These files are frozen output from Picker on branch `feat/aggregate-views`
(version `2.0.0-feat/aggregate-views-cd66621-2026-11-16`, tool commit
`v2.6.0-13-gcd66621`) so the interface tests can run without a local Picker
checkout. Regenerating them with that binary reproduces these bytes exactly. `tests/interfaces/test_picker_metadata.py`
also regenerates and re-exports the first fixture when `PICKER_BIN` (or the
default `/home/xyl/picker/build/bin/picker`) and its source file are present.
Those runtime tests are skipped otherwise; the frozen-metadata tests are not.

## `picker_aggregate_views_metadata.json`

Source: `/home/xyl/picker/test/data/aggregate_views.sv` (module
`AggregatePorts`). It covers a packed struct with a signed descending packed
vector, a signed two-dimensional packed array with a negative bound, an
unpacked struct with nested unpacked arrays and packed members, and an
unpacked array of packed structs.

```sh
cd /home/xyl/picker
rm -rf /tmp/aggout
./build/bin/picker export test/data/aggregate_views.sv \
    --sim verilator --sname AggregatePorts --tname AggDUT --tdir /tmp/aggout
./build/bin/picker config /tmp/aggout/AggDUT.xscfg --section metadata \
    > /home/xyl/ucv4xs/.tools/sources/toffee/tests/interfaces/fixtures/picker_aggregate_views_metadata.json
```

## `picker_collisions_metadata.json`

Source: `picker_collisions.sv` in this directory. It covers the real
signal-tree shapes where one port is both a leaf and a parent (`data` with a
`0` child, `req` with a `valid` child).

```sh
cd /home/xyl/ucv4xs/.tools/sources/toffee
/home/xyl/picker/build/bin/picker export \
    tests/interfaces/fixtures/picker_collisions.sv \
    --sim verilator --sname Collide --tname CDUT --tdir /tmp/collout
/home/xyl/picker/build/bin/picker config /tmp/collout/CDUT.xscfg \
    --section metadata \
    > tests/interfaces/fixtures/picker_collisions_metadata.json
```

## `picker_packed_struct_array_metadata.json`

Source: `picker_packed_struct_array.sv` in this directory. It covers a packed
array whose element is a packed struct (`T::s_t [2:0] arr`) so the array must
remain an array of element views instead of collapsing to one packed leaf.
Both spellings `T::sarr_t arr` and `T::s_t [2:0] arr2` produce the same Layout.

```sh
cd /home/xyl/ucv4xs/.tools/sources/toffee
/home/xyl/picker/build/bin/picker export \
    tests/interfaces/fixtures/picker_packed_struct_array.sv \
    --sim verilator --sname M --tname PDUT --tdir /tmp/pkout
/home/xyl/picker/build/bin/picker config /tmp/pkout/PDUT.xscfg \
    --section metadata \
    > tests/interfaces/fixtures/picker_packed_struct_array_metadata.json
```
