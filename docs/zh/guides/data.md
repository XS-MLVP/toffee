# 信号、Bundle 与数组

## XData

DUT 信号使用 XData。需要检查或取得底层对象时调用：

```python
from toffee import as_xdata

signal = as_xdata(dut.data)
```

绑定、位宽检查、读写和原生触发条件都使用同一个 XData 对象。

## Bundle

`Bundle` 把多个信号组织在一起，方便按名称访问、采样和驱动：

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

`sample()` 返回不可变的 `BundleValue`，保存这次读到的值，可交给检查器比较或用于覆盖率采样。

叶子值是保留位宽与 X/Z 的 LogicValue。模型需要整数时显式转换，例如
`opcode = snapshot.opcode.as_int()`；遇到 X/Z 会报错，避免把未知值静默当作有效输入。
`payload` 连接的是当前信号，重新读取时可能已是下一拍的值。
`snapshot` 保留这次采样结果；异步处理时应传递快照，不要事后重读引脚。

<a id="unpacked-arrays"></a>

## 定长 unpacked 数组

Picker 生成的数组可直接放进 Bundle；Toffee 自动适配为 `SignalArray`，叶子仍是原来的
`XData`。例如 RTL 声明 `logic [64:0] matrix [3:2][-1:1]`：

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

下标是视图的真实坐标，负数不是倒数位置；迭代按声明顺序，最后一维变化最快。
`SignalArray(source)` 也可显式适配公开提供 `bounds/shape/indices` 和索引能力的视图，
或者 xcomm 的 `XDataArray`。不依赖 Picker 的私有 Python 类，也不复制、重新绑定叶子。
构造适配器不读取或展开全部元素；采样整组或声明整组驱动所有权时才遍历叶子。

`sample()` 返回的数组部分是不可变 `ArrayValue`，保留边界及四态 `LogicValue` 叶子。
`compile_sampler()` 缓存结构和 getter，但每次调用仍读取当前值。`ArrayValue.as_tuple()`
显式转为位置序列，保留四态叶子但丢弃边界；`as_dict()` 返回真实整数下标到普通整数的
嵌套映射，遇到未知位报错。`BundleValue.as_dict()` 对数组使用同样的映射。

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

数组驱动接受边界一致的 `ArrayValue`、按真实下标组织的 mapping，或按**声明顺序**
排列的嵌套 list/tuple。完整数组的形状、下标和未知值会在写入前检查；不承诺模拟器
写入失败后的事务回滚。结构只读，不替换元素；不支持切片、动态下标或动态数组。
Scoreboard 会核对快照边界，并用 `matrix[3][-1]` 这样的真实下标报告差异。

端口数组使用 RTL 边界；内部原生数组继续使用其来源提供的零起始存储坐标：

```python
from toffee import SignalArray

memory = SignalArray(dut.xcfg.NewXDataArrayView(name))
memory[0][0].AsImmWrite()
memory[0][0].Set(42)
```

适配器不会改变写模式、推进时间或自动注册 `XPort`。生成端口的边沿提交仍由原来的
`XPort`/`XClock` 管理；动态查询的内部叶子由调用方选择立即写或显式提交。
只取得旧扁平列表时仍按位置序列处理，不猜测 RTL 边界。使用 Picker/xcomm 的新数组
能力需一起重建 ABI 4 库、绑定和 DUT；普通信号不要求 `XDataArray`。
可运行的真实 RTL 场景见 [数组示例](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/arrays/README.md)。

## PackedArray 与 PackedLayout

等宽一维总线：

```python
from toffee import PackedArray

lanes = PackedArray(dut.lanes, element_width=32, count=4)
lanes[2].Set(0x1234)
```

嵌套结构和多维布局：

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

这些对象直接访问底层信号，不复制仿真器中的存储，也不替你选择采样时刻。

## 从 DUT 名字或 Picker metadata 绑定

用 Field 声明来源和预期位宽，绑定时即检查接口变化：

```python
from toffee import Bundle, Field

request = Bundle.bind(dut, {
    "address": Field("req_addr", width=32),
    "payload": {"data": Field("req_data", width=64)},
})
address_only = request.view_as({"addr": Field("address", width=32)})
assert address_only.addr is dut.req_addr
```

`Bundle.bind_tree(dut, signal_tree, source_prefix="io")` 接受 Picker 的信号树 metadata，
保留底层信号身份并核对位宽；Python 关键字如 `in` 映射为 `in_`，连续数字索引成为序列。
树和前缀由项目显式提供，不从信号名字推断协议。`fields`/`leaves()` 可供组件枚举信号。
`Bundle.drive()` 要求完整且无多余字段的形状，不会把漏掉的字段隐式保持或归零。
带 `Unpacked: [[left, right], ...]` 的端口节点绑定成 `SignalArray`，检查各维边界及每个
元素的位宽。`Field(("matrix", 3, -1), width=65)` 可显式选择一个数组叶子。

## 位序、padding 与四态值

默认 `lsb_first=True` 把第一个字段/元素放在低位，False 则放在高位。
每层 array/struct 独立决定位序；需要空洞时使用 stride、结构宽度和显式 offset：

```python
layout = PackedLayout.struct({
    "opcode": PackedLayout.bits(4, offset=0),
    "payload": PackedLayout.array(2, PackedLayout.bits(8), stride=16, offset=8),
}, width=40)
packet = PackedView(dut.packet, layout)
packet.payload[1].Set(0xA5)
```

同一 struct 内不能混合显式与自动 offset；范围重叠默认报错，仅有意建立别名时使用
`allow_overlap=True`。drive 写入布局叶子，padding 保留原值。`split_packed()` 是等宽
`PackedArray` 的便利入口。这些视图要求支持子信号的原生 XData。

`LogicValue(value, x_mask, width)` 保存 aval/bval。`as_int()` 遇到 X/Z 会报错，先用
`is_known` 判断；`as_int(strict=False)` 仅用于明确接受未知位的诊断，不代表硬件真值。
`BundleValue.as_dict()` 得到包含快照叶子的普通结构，快照不会随 DUT 后续改变。

Driver 的 signal ownership 以底层信号身份检查；另建 Bundle 视图不会绕过冲突检查。
它识别同一个 `XData`，不按原生地址猜测不同 `XData` 的物理别名；同一元素应复用同一来源视图。
见 [Driver](drivers.md) 与 [Monitor](monitors.md)。
