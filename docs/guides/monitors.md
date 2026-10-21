# Monitor：采样与观察

Monitor 负责在指定时刻读信号，保存结果，再交给检查器处理。
这样即使处理任务稍后才运行，也不会漏掉只出现一拍的输出。
一般从 `SamplingMonitor` 开始，采样条件由设计的有效信号和时序决定。

每条记录是 `Transfer(event=event, value=snapshot)`，包含采样事件和快照。
它与 `Driver.submit()` 返回的 `XTransfer` 不同：前者是观察到的数据，后者用于等待一笔请求完成。

## 采样模板与关闭

`SamplingMonitor` 在显式 trigger 命中时同步调用 `capture(event)`，返回的 `Transfer`
保留该事件及其 tick/phase。使用 `Bundle.sample()` 或不可变值保存快照，不要返回
可变 DUT 对象；框架不替调用方猜测复制方式。capture 不能是异步函数或返回 awaitable。

```python
from xreactor import Bundle, Execution, RisingEdge, SamplingMonitor

outputs = Bundle(data=dut.data)
monitor = SamplingMonitor(
    RisingEdge(dut.clock), capture=lambda event: outputs.sample(), capacity=64,
)
async with Execution(backend) as execution:
    async with monitor.start(execution):
        first = await monitor.recv()
        second = await monitor.recv()
```

逐周期采样并不表示每周期都有一条有效响应。可显式提供有效事件 trigger；对固定延迟
数据通路也可配合 `Scoreboard.bind(latency_cycles=..., sampled=True)`，仅检查目标窗口。
模板不推断接受时刻；ReadyValidMonitor 保留自身的 capture/接受时刻规则，两者共用私有
交付与生命周期实现。完整固定延迟示例见[事务检查示例](../examples.md)。

两个内置 Monitor 都遵循以下关闭契约：

- `aclose()` 终止订阅、释放 watcher 和内部任务，丢弃未读缓存；不隐式排空。
- 等待中的所有 `recv()` 被唤醒。正常关闭抛 `MonitorClosedError`；`async for` 正常结束。
- capture/decoder/交付失败优先传播原始异常；经 `recv()` 观察后，退出不重复报告同一错误。
  无人接收的错误从 `aclose()` 或 context 退出报告，独立的清理错误一起保留。
- 关闭后不能重启；创建新实例。多批次在同一次启动期间完成。
- 取消单个 `recv()` 等待者不关闭 Monitor。上下文的取消会清理该组件，不取消宿主任务。

绑定 Scoreboard 时默认由 Scoreboard 管理 Monitor，不要另行 start、关闭或增加 recv 消费者。
由 Agent 封装时，内部自动完成响应接线和唯一生命周期管理，见[Agent 与模型](agents-and-reference-models.md)。
先调用有预算的 `finish()` 检查残余事务，再退出；关闭清空缓存不能代替严格结束检查。

## 按场景选择交付方式

| 场景 | 配置 | 超过 capacity 时 |
| --- | --- | --- |
| 事务核对、必须逐笔统计的覆盖率 | 默认 `delivery="lossless"` | 抛 MonitorOverflowError，不默默丢事务 |
| UI 或只关心最新值的遥测 | `delivery="latest"` | 丢最旧记录，保持有界缓存；capacity=1 仅保留最新值 |

Monitor 没有广播语义，多次 recv 从同一队列竞争取走记录。检查与覆盖率共用一条观察时，
由唯一消费者先采样覆盖率再交给检查器，见[运行配方](verification-flow.md)。

## 作为被动 Agent 使用

```python
monitor = SamplingMonitor(
    RisingEdge(clock), capture=lambda event: output.sample(),
    delivery="latest", capacity=1,
)
agent = Agent("telemetry", monitors={"output": monitor})
async with Execution(backend, agents=[agent]):
    observation = await agent.recv("output")
    snapshot, tick = observation.value, observation.event.tick
```

项目已知的有效条件可写成
`PythonPredicateTrigger("output-present", present, sample=RisingEdge(clock), mode="each_sample")`，
再将它传给 SamplingMonitor。逐周期采样与“有一笔有效响应”是不同的契约。
如果响应检查已经占用某个 Monitor，使用返回的事务结果，不要同时 recv 该响应流。

需要协议自身的候选采样和接受规则时，见[可选协议组件](protocols.md)。自定义 Monitor
实现 start/recv/aclose；也可直接复用 SamplingMonitor 的同步 capture 模板。
