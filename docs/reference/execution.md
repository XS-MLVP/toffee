# Execution 参考

导入：`from toffee import Execution`。配置步骤见
[Execution 与 Backend](../guides/execution-and-backend.md)，
调度原因见[仿真调度与资源归属](../explanation/execution-model.md)。

## 构造参数

`backend` 必填，可按位置或关键字传入；其余参数仅接受关键字传入。

| 参数 | 默认值 | 含义与约束 |
| --- | --- | --- |
| `backend` | 必填 | 提供 half-step 和 stable sampling 能力的 `SimulationBackend` |
| `agents` | `()` | Agent 实例的 iterable；实例不能重复，各 Agent 不能共享组件 |
| `coverage` | `()` | `CoverGroup` 实例的 iterable；实例不能重复，由 Execution 启停采样 |
| `default_sample` | `None` | 条件的默认采样阶段；指定时须为 `RisingEdge`、`FallingEdge` 或 `DriveStable` |
| `max_batch_ticks` | `4096` | 单次 Backend 调用最多推进的 half ticks，须大于 0 |
| `quantum_ms` | `10.0` | 单次连续推进的墙钟时间预算，单位毫秒，须大于 0 |
| `budget_check_interval` | `64` | 原生执行器检查时间预算的间隔，须大于 0 |
| `max_settle_rounds` | `100_000` | 单次阶段收敛允许交回 asyncio 的最大轮数，须为正整数，不接受布尔值 |

前三个推进预算不替代测试超时或事务响应预算；`max_settle_rounds` 也不表示仿真周期数。
没有配置 `default_sample` 时，需要采样阶段的条件应显式传入 `sample`，
见[Trigger 用法](../guides/triggers.md)。

## 作用域与方法

| 入口 | 契约 |
| --- | --- |
| `async with Execution(...) as execution` | 取得 Backend 控制权，启动注册的覆盖组与 Agent；退出时清理本次资源并释放控制权 |
| `execution.paused()` | 异步 context manager；暂停仿真推进，宿主 asyncio 继续运行；嵌套暂停全部退出后恢复 |
| `execution.external_task(coro, *, name=None)` | 在当前活跃 Execution 中创建域外原生 Task；保留用户 ContextVar，清除隐式 Reactor 绑定；返回 Task |
| `execution.subscribe(spec)` | 在活跃 Execution 中注册绑定后的订阅规格，返回 `Subscription` |

正常退出、测试体异常和启动失败都会执行资源清理。Agent 反向关闭，覆盖组同步并停止，
本次等待、订阅与编译状态被清理。`Execution` 不关闭 Backend，不复位 DUT，也不结束
调用方独立创建的后台任务。创建者分别负责 `backend.close()`、DUT 释放和外部 Task 清理。

同一 Backend 同时只能属于一个活跃 Execution。复用 Backend 不会复位时钟或 DUT；
已关闭的 Agent 与内置 Monitor 应新建实例。

## XClock 控制权

| 时段 | 手动推进行为 |
| --- | --- |
| 仅创建 `XCommClockBackend`、尚未进入 Execution | 尚未取得控制权，可用 `dut.Step(...)` 初始化 |
| Execution 内，包括 `paused()` 期间 | 原生 XEngine 独占 XClock，禁止手动推进和复位 |
| Execution 退出并释放控制权后 | 恢复手动控制；释放本身不会推进或复位时钟 |

作用域内的 `dut.Step`、`clock.Step`、`RawStep`、`StepHalf` 和 `Reset` 会报错。
旧接口 `AStep`、`ANext`、`ACondition`、`RunStep` 也不能混用。
不同 Backend 包装同一个 XClock，不能同时接管它。

若退出时停在半周期中间，手动调用完整周期 `Step` 前需用 `StepHalf` 完成剩余半周期。
直接使用 Backend 的 `arm` / `run_until` 会在首次调用时接管时钟并持有到 `close()`；
进入 Execution 前须先移除直接注册的 watcher。

## 收敛与并发限制

超过 `max_settle_rounds` 会抛出 `SimulationNotSettledError`，诊断包含 tick、phase 和
未完成回调。此时不会跳过就绪工作强行推进。`paused()` 内不能等待依赖仿真推进的 Trigger。

Backend、XClock 与 DUT 信号不支持跨线程并发访问；多个 Execution 不提供共同时间轴。
asyncio loop 的适配范围与外部事件边界见[行为与限制](semantics-and-limitations.md)。
