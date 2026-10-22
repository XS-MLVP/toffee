# Trigger

Trigger 描述一个等待条件。`await trigger` 返回对应的 `XEvent`。
以下片段在当前 Execution 内使用，clock 与 dut 由项目环境提供；可直接运行的完整测试
见[输入与时钟教程](../getting-started/clock-and-input.md)。

## 时钟

普通 case 优先按周期表达等待；驱动和采样的具体时机由 Driver、Monitor 封装：

```python
await ClockCycles(dut.clock, 1)
await ClockCycles(dut.clock, 10)
```

`ClockCycles(clock, n)` 等待注册之后的 n 个上升沿完成，并返回最后一个上升沿的
稳定事件。从任意时刻开始，第一次等待未必耗时一个完整周期；连续调用则可以按拍
组织测试。有背压的协议由 Driver 等待实际接受，不能把“经过一拍”直接当作握手成功。

协议组件和故障注入测试需要精确的阶段控制时，使用边沿或驱动稳定阶段：

```python
await FallingEdge(dut.clock)
await RisingEdge(dut.clock)
await DriveStable(dut.clock)
```

`FallingEdge` 和 `RisingEdge` 在稳定的 half-step 返回。下面是在下降沿准备输入、
在下一上升沿由 DUT 接受的组件内部写法，适用于明确在上升沿采样的接口：

```python
await FallingEdge(dut.clock)
dut.valid.Set(1)
dut.data.Set(payload)
await RisingEdge(dut.clock)
```

## 信号条件

```python
await Value(dut.ready, 1, sample=RisingEdge(dut.clock))
await ValueChange(dut.response, sample=RisingEdge(dut.clock))
```

省略 `sample=` 时使用 `Execution(default_sample=...)`。条件从注册后的下一个采样点开始
求值。

`Value` 支持三种命中模式：

| `mode` | 命中条件 |
| --- | --- |
| `"enter"` | 条件由 false 变为 true，默认值 |
| `"each_sample"` | 条件为 true 的每个采样点 |
| `"change"` | 条件的布尔结果发生变化 |

`Value` 和比较表达式支持任意位宽无符号 XData。`ValueChange` 比较完整的 aval/bval。

## 超时

`SimTimeout` 按仿真周期计时；`WallTimeout` 按秒计时。两者到期都返回
`XEventKind.TIMEOUT`，**不会自动抛异常**。要让等待超时使测试失败，应显式处理结果：

```python
from toffee import AnyOf, RisingEdge, SimTimeout, Value, XEventKind

event = await AnyOf(
    Value(dut.result, wanted, sample=RisingEdge(clock)),
    SimTimeout(100, clock=clock),
)
if event.kind is XEventKind.TIMEOUT:
    raise TimeoutError("result was not observed within 100 cycles")
assert event.value == wanted
```

这里的 clock、信号、期望值和预算由测试提供。组合等待返回时仿真可能已经继续推进，
检查命中时的数据应使用事件值或订阅的 `capture` 快照，而不是重读 live signal。
使用 `asyncio.timeout(seconds)` 可以另外约束整个测试的墙钟时间。

## asyncio 事件

```python
await AsyncioEventTrigger(stop_event)
item = (await QueueTrigger(queue)).value
result = (await TaskComplete(task)).value
```

## 组合

```python
event = await AnyOf(
    Value(dut.done, 1, sample=RisingEdge(dut.clock)),
    AsyncioEventTrigger(stop_event),
    WallTimeout(1.0),
)
if event.kind is XEventKind.TIMEOUT:
    raise TimeoutError("neither DUT completion nor stop event arrived")

event = await AllOf(
    ClockCycles(dut.clock, 10),
    TaskComplete(reference_task),
)
```

`AnyOf` 返回最先完成的事件，并取消其余等待。`AllOf` 等待全部事件，子事件保存在
`event.causes` 中。

恢复等待时已有多个来源完成，AnyOf 按参数顺序选择，即使这些事件的 tick 不同；
它既不按 tick 仲裁，也不保证先排空一个仿真 phase 中所有待交付通知。
因此上述写法处理的是获胜的超时事件，不提供精确周期的 deadline 保证。
需要拒绝迟到响应、且让截止周期内响应优先于超时的规则时，使用
[Scoreboard 的响应预算](scoreboard.md)，不要把通用组合等待当作事务 deadline 检查。

已有 asyncio task 应通过 `TaskComplete(task)` 传入：它把结果包装为 XEvent，并保护
原任务。直接传入 task 时，AnyOf 会取消未获胜的原 task；AllOf 在失败清理时也会取消
尚未完成的来源。完整的所有权配方见[asyncio 与 pytest](asyncio-and-pytest.md)。
