# 原生表达式与资源管理

`XCommClockBackend` 把支持的条件交给 xcomm 执行。
这页说明数据运算、未知值和资源释放中容易遇到的问题。
安装见[原生后端与项目 DUT](../getting-started/native-and-next.md)，
所需提交、包版本、ABI 和协议版本以
[依赖清单](https://github.com/XS-MLVP/toffee/blob/main/.github/native-dependencies.json)为准。

## 逻辑运算和数据位运算不是一回事

在 Trigger 中，`&`、`|`、`~` 用来组合真假条件。
对数据的每一位进行运算，使用 `bit_and()`、`bit_or()`、`bit_xor()` 和 `bit_not()`。
不要用 Python 的 `and`、`or`、`not` 代替可编译条件。

```python
from toffee import bits
from toffee.ir import signal_expr

data = signal_expr(pins.data)
negative = data.as_signed() < 0
low_byte = data.bit_and(255)
extended = data.as_signed().resize(129)
wrapped = (bits(255, 8) + 1) == 0
```

## 位宽和有符号数

Python 整数不会自动溢出，硬件位向量会。原生表达式按显式位宽运算：

| 写法 | 含义 |
| --- | --- |
| `bits(value, width, signed=...)` | 指定位宽和符号，常量越界时报错 |
| `as_signed()`、`as_unsigned()` | 位数不变，改变符号解释；8 位 `0xff` 按 signed 解释是 -1 |
| `resize(width)` | 按原符号类型扩展，或截去高位 |
| `resize(width, signed=...)` | 先扩展或截断，再按目标符号类型解释 |
| `+`、`-`、`*`、一元负号 | 结果按位宽截断；8 位的 255 加 1 得到 0 |
| `//`、`%` | 有符号除法向零截断，余数与被除数同号；除零报错 |
| `<<`、`>>` | 有符号右移补符号位，负移位量报错 |
| 比较 | 两个带类型的操作数须同位宽、同符号类型，结果为布尔值 |

普通整数常量会按另一操作数的类型检查。
例如 `data.as_signed() == -1` 有明确含义，unsigned 信号不能直接与 -1 比较。
两边类型不同时，先显式转换，不依赖隐式提升。

## 未知值与支持范围

上述数据运算按两态位向量定义，不提供完整的 SystemVerilog 四态传播。
不要用它代替需要检查 X/Z 传播的模型。
XData、LogicValue 的四态读写是另一层能力，详见[信号与 Bundle](data.md)。

选择严格原生执行时，不支持的表达式会报错，不会悄悄交回 Python。
允许自动回退的覆盖模型会在报告中记录回退原因。

## 长时间运行时怎样释放程序

后端会缓存近期编译的程序，默认最多 256 份，可通过
`XCommClockBackend(clock, program_cache_capacity=256)` 调整，设为 0 可关闭缓存。
缓存数量不是正在运行的 Trigger 数量，也不是 C++ 表达式节点的固定上限。

正在等待的 Trigger 和正在采样的覆盖组，不会因为缓存淘汰而失效。
后端在安全位置回收不再使用的表达式；需要主动回收时，可调用 `backend.collect_programs()`。
这不会清空仍在匹配的 Sequence、FSM 或覆盖率计数。

`Execution` 退出会清理本次等待与采样注册，但不会关闭你创建的 backend。
backend 的创建者仍应在 `finally` 中调用 `close()`。
只要仍有程序引用 DUT 的信号，就不要提前销毁 DUT。
