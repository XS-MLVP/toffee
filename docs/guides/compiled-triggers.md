# 编译条件、Sequence 与 FSM

## `@xtrigger`

`@xtrigger` 把条件编译成引擎可执行的表达式。使用 `XCommClockBackend` 时，
这些条件由 C++ 引擎判断，不需要 Python 每拍轮询：

```python
from toffee import RisingEdge, xtrigger


@xtrigger(sample=RisingEdge("clk"))
def handshake(dut):
    return dut.valid & dut.ready & ~dut.flush


event = await handshake(dut)
```

布尔组合必须使用 `&`、`|`、`~`，不能使用 Python `and`、`or`、`not`。支持信号、
无符号常量和比较表达式；位宽检查发生在 arm/编译 阶段。

需要有符号数和数据运算时，使用 `as_signed()`、`as_unsigned()`、`resize()` 等接口，
详见[位宽与表达式规则](expressions-and-resources.md)。
逻辑 `&`、`|`、`~` 保持原含义，位运算使用 `bit_and/or/xor/not()`。

装饰器只声明规格，调用 `handshake(dut)` 才生成绑定 DUT 的 Trigger。`sample` 可以写
真实 phase trigger，也可以像上例一样使用 DUT 属性路径字符串。

## `@pytrigger`

`@pytrigger` 用于调用 Python 条件函数：

```python
from toffee import pytrigger

allowed_values = {1, 2, 3}

@pytrigger(sample=RisingEdge("clk"))
def model_accepts(dut):
    return dut.data.U() in allowed_values
```

条件函数 是同步函数，并在指定的 sample phase 执行。异步数据源使用
`QueueTrigger`、`AsyncioEventTrigger` 或 `TaskComplete`。

## 非重叠 Sequence

```python
from toffee import Hold, Next, Sequence, Wait, Within

@xtrigger(sample=RisingEdge("clk"))
def request_then_ack(dut):
    return Sequence(
        Wait(dut.req & ~dut.flush),
        Within(1, 8, dut.ack),
        Hold(dut.valid, cycles=2),
    )
```

- `Wait(expr)`：等待条件；
- `Next(expr)`：紧接的下一个 sample 必须满足条件，否则重新等待序列起点；
- `Within(min, max, expr)`：在周期窗口中等待；
- `Hold(expr, cycles=N)`：要求连续保持 N 个 sample；
- 当前 Sequence 是非重叠匹配，一个 registration 维护一个活动状态。

这些对象识别观察到的模式。Within 超出窗口回到 Sequence 开始；Hold 失配只清零
当前保持步骤的连续计数，继续等待同一个 Hold。二者都**不会自动抛出断言失败**；
一次完整模式命中才产生事件。测试需要失败期限时，应显式处理
[超时](triggers.md)。用于发送激励的场景仍可用普通 async 函数组合。

## 显式 FSM

```python
from toffee import FSM, State

@xtrigger(sample=RisingEdge("clk"))
def request_fsm(dut):
    return FSM(
        start="WAIT_REQ",
        states={
            "WAIT_REQ": State().when(dut.req).goto("WAIT_ACK"),
            "WAIT_ACK": State()
                .when(dut.flush).goto("WAIT_REQ")
                .when(dut.ack).trigger("ACK"),
        },
    )
```

同一 state 的分支按声明顺序判断。terminal trigger 产生 `FsmEvent`，terminal 名称位于
`event.terminal_state`。


持续采样与 handler 交付见[订阅](subscriptions.md)；需要接收事务记录时见 [Monitor](monitors.md)。

Within 的最小/最大值均包含边界，进入该步骤后的下一个 sample 年龄为 1；一个 sample
至多完成一个步骤。包括过早命中、上界命中、窗口超时重启和 Hold 间断的可运行回归：

```bash
python3 -m pytest -q tests/integration/test_compiled_patterns.py --require-xspcomm
```

同样的采样表在 MemoryBackend 与原生引擎上都检查命中 tick。

同一份编译条件也可绑定到 CoverGroup，匹配后在 C++ 内持续累计覆盖，详见
[绑定覆盖采集](coverage.md)。计数注册拥有独立匹配历史，普通 await 的注册和
返回事件语义保持不变。
