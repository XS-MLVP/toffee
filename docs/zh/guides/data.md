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

Picker 生成的数组可直接放进 Bundle；Toffee 自动适配为 `SignalArray`。它是结构只读的
有界聚合，保留原 `XData` 叶子的对象身份。例如 RTL 声明 `logic [64:0] matrix [3:2][-1:1]`：

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
包括 xcomm 的 `XArray`。不依赖 Picker 的私有 Python 类，也不复制、重新绑定叶子。
构造适配器不读取或展开全部元素；采样整组或 Driver 获取整组所有权时才遍历叶子。

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
写入失败后的事务回滚。结构只读，不替换元素；叶子本身仍是实时信号。不支持切片、
动态下标或动态数组。Scoreboard 会核对快照边界，并用 `matrix[3][-1]` 这样的真实下标
报告差异。

端口数组使用 RTL 边界；原生数组使用相同的递归 `XArray` 接口，Picker 的
`NewXArray` 对内部 mem_direct 数组报告零起始存储坐标：

```python
from toffee import SignalArray

memory = SignalArray(dut.xcfg.NewXArray(name))
memory[0][0].AsImmWrite()
memory[0][0].Set(42)
```

适配器不计算元素地址、DPI 下标或后端专用句柄，只使用来源视图暴露的叶子。它不改变
写模式、不推进时间，也不额外注册 `XPort`。生成端口的边沿提交仍由原来的
`XPort`/`XClock` 管理；动态查询的内部叶子由调用方选择立即写或显式提交。扁平列表仍是
位置序列，不猜测 RTL 边界。

原生数组视图使用 xcomm 的 `XArray` 工厂（原生 ABI 4）和匹配的 Picker 生成绑定；
原先单独的原生数组协议已移除，升级工具链后重新生成 DUT 并迁移为递归索引。

DPI 和 mem_direct 数组路径已在 Verilator **5.026、5.048、5.052** 上验证。可运行的真实
RTL 场景见
[数组示例](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/arrays/README.md)。

## RTL struct 与递归数组

使用带聚合支持的配套 Picker/xcomm（**原生 ABI 4**）时，可把 `XStruct` 和 `XArray`
直接交给现有 Toffee 组件：

```python
from toffee import Bundle

payload = Bundle(
    record=dut.record_in,
    header=dut.packed_in_view,
    matrix=dut.matrix_view,
)
snapshot = payload.sample()
word = snapshot.record.entries[-1].payload[3].as_int()
payload.drive(snapshot)
```

`Bundle(xstruct)` 适配 struct 根节点，数组适配成 `SignalArray`，包括 struct 数组。
快照递归组合 `BundleValue`、保留边界的 `ArrayValue` 和四态 `LogicValue`。字段名、声明顺序、
各维方向及负下标均来自来源视图。原生字段与 Bundle 方法重名时用 `bundle["sample"]` 这样的
索引访问；普通用户编写的 Bundle mapping 仍拒绝保留名。

packed 标量字段保留来源视图暴露的完整 `XData` 值；需要逐维访问 packed 数组字段时，显式把
`xstruct.Array("field")` 交给 `SignalArray`。packed 视图共享父存储，unpacked 字段保留各自
独立叶子。Toffee 不计算偏移、不复制聚合存储、不增加时钟回调或 XPort 条目，也不改变写模式。
需要 Driver 识别共同叶子时，复用同一来源视图。

`ReadyValid`、Driver、Monitor、编译采样器、原生叶子条件及 coverage 均使用这些原始叶子。
整棵结构的 drive 会在首次写入前检查字段、数组边界、X/Z 驱动值、输出方向和只读后端。
四态采样保留未知位；驱动含 X/Z 的 `LogicValue` 仍会报错，不会静默丢弃未知位。

`dut.GetInternalSignal(name, use_vpi=True)` 返回的 VPI 来源也使用同一适配器。
驱动 VPI 输入前先完成模型初始化，避免 Verilator 首次求值覆盖提前写入的值。
动态查询的 VPI 输入不由生成的 XPort 提交，需显式选择立即写或安排提交后再推进仿真。
不要为 Picker 未导出布局的内部 packed struct 猜测字段偏移。

[聚合 RTL 示例](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/aggregates/README.md)
在 Verilator 5.052 上验证 DPI、mem_direct 和 VPI。Picker 的聚合端口要求 5.048+；已验证的
VPI unpacked struct 路径要求 5.052+。这些结果不代表 UVS、VCS 或 GSIM 的聚合运行时已支持。

## PackedArray 与 PackedLayout

`PackedLayout.struct` 仍是手工描述已有 packed `XData` 位向量的软件视图，独立于 Picker 的
RTL `Layout` 元数据。它不推断 RTL 类型，也不扩展 union 等不支持的端口类型。对于已支持的
RTL struct 和 packed 数组，优先使用上面的生成 `XStruct` / `XArray`，而非手工复制布局。

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
端口的 `Layout` 元数据还可绑定递归 struct 和数组，并检查字段、边界和位宽；packed 根节点
使用 xcomm 的 `StructView` / `ArrayView` 工厂，不猜测模拟器布局。元数据必须是无歧义的树；
如果同一节点既是端口叶子又是其他端口名的前缀，应显式绑定所需端口，而不是接受歧义节点。

使用只交付一份 `.xscfg` 配置库的配套 Picker 时，显式读取端口元数据，不需要单独的
`signals.json`：

```python
import json
from toffee import Bundle

pins = Bundle.bind_tree(dut, json.loads(dut.xcfg.Metadata("ports")))
```

`bind_tree` 通过生成属性解析端口。如果 Picker 为保护封装成员或方法而修改了端口属性名，
请用 `dut["original_name"]` 或生成后的属性显式绑定该端口，不要假设原属性一定是信号。
此适配器不会补造缺失的类型布局或原生地址。

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
