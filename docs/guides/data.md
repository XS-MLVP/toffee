# 信号、Bundle 与 packed 数据

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
见 [Driver](drivers.md) 与 [Monitor](monitors.md)。
