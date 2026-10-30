# Scoreboard 与流水线事务

`Scoreboard.check()` 比较调用方提供的 expected/actual；异步模式则消费 driver 的接受通知和
monitor 的观察值，关联后复用同一比较入口。框架不定义握手、reset 或 flush 规则。

## 按任务选择接口

| 目的 | 接口 | 返回或结束条件 |
| --- | --- | --- |
| 比较已经得到的两个值 | `scoreboard.check(expected=..., actual=...)` | 比较完成，无需绑定 Driver/Monitor |
| 等输入被接受 | `await driver.send(request)` | 返回接受事件，不等待响应 |
| 提交输入，不跟踪响应 | `async_driver.send(request)` | 立即返回 XTransfer，接受时自动完成，await 得到接受事件 |
| 提交并自动检查响应 | `scoreboard.submit(request)` | 立即返回 XTransfer，由 Scoreboard 关联并完成 |
| 自己处理响应 | `driver.submit(request)` | 返回 XTransfer，调用方负责关联并完成句柄 |
| 等一笔响应 | `await transfer`（由 submit 返回） | 返回响应或抛出失败；提交一笔就等一笔会使场景串行 |
| 等当前批次，随后还要提交 | `await scoreboard.drain(timeout_cycles=...)` | 等调用前提交的事务，不代表严格结束 |
| 结束提交并检查 | `await scoreboard.finish(timeout_cycles=..., observe_cycles=...)` | 排空、有限观察、检查残余输入输出 |
| 退出或异常清理 | context manager / `aclose()` | 检查当前状态并释放资源，不等待 DUT 排空 |

`Transfer` 是 Monitor 交付的观察记录；`XTransfer` 是跟踪接受与完成的句柄。
`SyncDriver.send()` 是 async 函数，其 Sync 表示调用方等待输入完成。
`AsyncDriver.send()` 和 `submit()` 都在调用时提交并立即返回句柄：前者等待接受，
后者等待响应；二者都不阻塞 Python event loop。“调用即提交”是 AsyncDriver 模板
及其子类的保证，不向 Driver/SignalDriver 基类或其他具体 Driver 强加后台执行能力。

input-only 句柄的接受就是完成，不需要伪造响应，也不进入 recv_accepted 流。
如果 DUT 实际会返回需要检查的响应，应通过 scoreboard.submit 跟踪；send
不会抑制 DUT 输出，也不会让 Scoreboard 忽略未匹配响应。

## 直接检查

```python
from toffee import Scoreboard

scoreboard = Scoreboard("prediction")
scoreboard.check(expected=wanted, actual=prediction)
```

默认递归比较 dataclass、mapping、sequence、BundleValue 和标量。失败抛出
`ScoreboardMismatch`（`AssertionError` 子类），包含字段路径、期望、实际值和事务上下文。
默认差异只计算一次，最多保留 `max_differences=20` 项。可传入 `compare(expected, actual)`
函数或覆写 `compare()`；自定义断言保留为异常 cause。

## 异步检查与资源所有权

```python
async with (
    Execution(backend) as execution,
    prediction_driver as driver,
    Scoreboard(
        "prediction", expected=lambda request, _: model.predict(request),
    ).bind(
        execution, driver=driver, monitor=prediction_monitor,
        clock=clock, response_timeout_cycles=20,
    ) as scoreboard,
):
    for request in requests:
        scoreboard.submit(request)
    status = await scoreboard.finish(timeout_cycles=200, observe_cycles=2)
```

多项 async with 按顺序进入、逆序退出，与逐层嵌套等价。重复的环境创建可以放入项目
自己的 pytest fixture 或 asynccontextmanager，测试仍显式选择 finish 的预算。
backend 由创建者关闭，见 [Execution 与 Backend](execution-and-backend.md)。

`clock` 和 `response_timeout_cycles` 必须显式传入。绑定要求当前活动的 Execution，
一个 Scoreboard 只绑定一次。Driver 只需提供 `submit()` 和 `recv_accepted()`；Monitor
提供 `start(execution)`、`recv()`、`aclose()`。Scoreboard 独占 Monitor 的消费和启停；
Driver 的生命周期由外层 context manager 管理。

[Agent](agents-and-reference-models.md) 已在内部完成响应接线，普通用例只需 connect(ref)。
需要独立组合检查器和外部 Monitor 生命周期时，可传入 `manage_monitor=False`：
Scoreboard 独占消费，但不启动/关闭 Monitor，外层必须先启动它并在 Scoreboard 退出后关闭。
同一个 Monitor 不能同时由两个 Scoreboard 消费。默认 `manage_monitor=True` 保持原行为。

Monitor 在进入 Scoreboard context 或第一次提交时启动，跨提交批次保持运行，直到
`aclose()`。匹配完最后一笔事务不会停止监视，也不会调用 driver 的协议 idle/复位操作。
不使用 context manager 时，必须在 `finally` 中 `await scoreboard.aclose()`。

接受和观察事件都必须来自指定单 XClock 的 rising-stable phase。Monitor 应及时交付该
phase 的快照，不能在交付前等待任意 HTTP、wall delay 或未来仿真事件。周期预算以事件
的 half-tick 时间戳计算；框架在截止 rising phase 后的 falling 屏障检查缺失响应，暂停
推进并让就绪的 source consumer 处理完毕，因此截止周期内的正确响应优先于超时。

## 提交、预测与响应

```python
# 向量给出预期值；优先于 expected source。
transfer = scoreboard.submit(request, expected=wanted)

# 只有控制流需要结果时才等待；逐次等待会使激励串行化。
actual = await transfer
```

expected source 在 accepted stream 被消费时调用，按 driver 的接受通知顺序更新模型；
`CheckContext.accepted_event` 保留实际接受时间。既没有显式 expected、也没有 source
时抛出 `MissingExpectedError`。提交后不要修改 request 或 expected 对象。

source 必须同步，异步函数或 awaitable 返回值会明确报错。显式 expected 会跳过 source，
直接使用此回调接有状态模型时不要混用覆盖值。Agent.connect(ref) 则独立更新模型，
不会因为覆盖 expected 而跳过状态推进；更新顺序、异常和多接口边界见
[Agent 与 reference model](agents-and-reference-models.md)。

`XTransfer` 的状态为 PENDING → PROCESSING → COMPLETED，失败进入 FAILED。
`cancel()` 只撤销 PENDING 请求；PROCESSING 请求已经被接受，调用 cancel 会报错，仍需
等待其响应或超时。取消等待 transfer 的协程不会取消事务，也不算观察过它的失败。
异常关闭时，已接受但未完成的事务进入 FAILED，而非 CANCELLED。

## 关联与观察模式

`bind()` 支持三种一对一关联：

- 默认 FIFO：按接受顺序关联有效响应。
- `request_key=...`、`response_key=...`：按 key 关联，允许乱序完成；同时在途的重复 key
  抛出 `ScoreboardAssociationError`。
- `latency_cycles=N`：仅匹配 accepted tick 后 N 个周期的观察，支持 N=0；错过该周期
  也会主动超时，而非一直缓存迟到结果。N 不得超过 response timeout。

默认把每笔观察当作有效响应；额外、重复或无法关联的输出在严格结束检查时失败。
如果 monitor 每个周期都发布样本、包含空闲值，显式设置
`latency_cycles=N, sampled=True`，只检查目标窗口内的样本，忽略窗口外的值。
`sampled=True` 不能单独使用，也不能与 key 匹配组合。

特殊关联可以覆写同步、无副作用的 `associate(observation, pending)`：返回其中一项表示
匹配，返回 None 暂存观察，等待更多接受通知。它不运行参考模型、不比较数据、不等待事件。
这也允许观察先进入 Python 队列、对应的 accepted 通知随后到达。

## drain、finish 与关闭

| 预算 | 从何时开始 | 覆盖范围 |
| --- | --- | --- |
| `response_timeout_cycles` | 每笔输入被接受时 | 该事务的响应；固定延迟模式实际以 latency_cycles 为截止窗口 |
| `drain/finish(timeout_cycles=...)` | 本次方法调用时 | 排空，包括等待尚未接受的输入；finish 还包含 observe_cycles |
| `asyncio.timeout(seconds)` | 进入该 context 时 | 墙钟总时间，由调用方按测试环境选择 |

周期预算必须显式给出，框架不猜测 DUT 的合理延迟。

- `await drain(timeout_cycles=N)` 等待调用前的提交 watermark，后续新提交不扩大此次等待。
  已接受请求另受 response timeout 约束；drain 的预算也覆盖尚未被接受的输入。
- `await finish(timeout_cycles=N, observe_cycles=M)` 封闭提交、排空、继续观察 M 个周期，
  然后检查事务守恒。M 默认 0；总预算包含排空和观察窗口。finish 后不能再提交。
- `await aclose()` 不等待 DUT 排空；它处理已经就绪的通知、检查当前状态并关闭任务和
  Monitor。未完成请求报 `ScoreboardIncompleteError`，未匹配输出报
  `ScoreboardAssociationError`。它不是 finish 的无限等待版本。

## 定位超时与后台失败

`ScoreboardTimeoutError` 同时继承 `ScoreboardError` 和 `TimeoutError`。消息区分响应、
固定延迟、drain 和 finish 预算；finish 的排空阶段失败也标记为 finish。
无需解析消息，可直接读取以下属性：

| 属性 | 含义 |
| --- | --- |
| `name`, `operation` | Scoreboard 名称；operation 为 response / fixed_latency / drain / finish |
| `budget_cycles`, `elapsed_cycles` | 此次生效的周期预算与已过时间，后者可含半周期 |
| `start_tick`, `deadline_tick`, `current_tick` | 起始、截止和发现超时的 half-tick（迟到响应使用观察 tick） |
| `context` | 响应失败的 CheckContext，包含 sequence_id、request、accepted_event；总预算失败为 None |
| `status` | 失败发生时的统计快照 |
| `reason` | 截止时间已过，或剩余预算不足以容纳结束观察窗口等原因 |

例如输入在 tick 20 接受，响应预算为 4 cycles，截止 tick 为 28；在 tick 29 的
falling 检查发现缺失时，elapsed_cycles 为 4.5。这包含了截止采样后的半周期检查屏障。
若结束观察窗口已无法放进剩余预算，会立即报错，elapsed_cycles 可能尚未达到预算。

后台错误可从 await transfer、drain、finish 或关闭到达 pytest；同一个已观察失败不在
关闭时重复报告。context 中已有异常时仍清理；独立清理异常和原始异常一起保留为异常组。

构造参数 `capacity=64` 分别限制活跃请求和未匹配观察数量，超限显式失败。
已完成事务从内部活跃表删除，统计累计保留；完整请求历史由调用方按需记录。
`ScoreboardStatus` 仅用于诊断，不替代 pytest verdict 或覆盖率。

可运行示例使用 MemoryBackend 和普通请求/响应队列，不需要 RTL 或任何握手协议：

```bash
python3 examples/transactions/scoreboard.py
```

源码见 [scoreboard.py](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/scoreboard.py)。

连续输入、FIFO/固定延迟/乱序 key 匹配，以及漏响应、迟到与关联错误的可运行演示，
见[流水输入与阶段重叠示例](pipeline-examples.md)。
