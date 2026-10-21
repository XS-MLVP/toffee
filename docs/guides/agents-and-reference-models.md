# Agent 与参考模型

Agent 把同一接口的 Driver、Monitor 和检查配置放在一起。
环境负责创建 Agent、连接参考模型，再交给 Execution 启动和关闭。
测试只需发送请求、等待完成并检查结果，不必每次重新连接这些组件。

```python
model = AccumulatorModel()
agent = AccumulatorAgent(dut, response_timeout_cycles=5)
agent.connect(model)

async with Execution(dut.backend, agents=[agent]):
    transfers = [agent.submit(command) for command in commands]
    await agent.finish(timeout_cycles=30, observe_cycles=1)
    results = [await transfer for transfer in transfers]
```

项目可以把唯一的 Execution context 放在环境或 pytest fixture 内，让用例直接使用
已经启动的 Agent 实例。`connect()` 只接线，不启动仿真，也不更新模型。

## 各层的职责

| 层次 | 负责什么 |
| --- | --- |
| Agent | 封装接口组件、事务操作、模型连接及响应检查配置 |
| Driver | 驱动输入，报告具体协议定义的接受事件 |
| Monitor | 把显式采样事件变为不可变观察记录 |
| Reference model | 按功能规格更新参考状态，返回本次接受输入的预期响应 |
| Scoreboard | 保存期望、关联实际响应、比较、检查超时和残余 |
| Execution | 启动注册的 Agent，退出时统一检查和释放组件及运行资源 |
| 项目环境/fixture | 拥有 DUT、模型和运行配置，管理初始化、seed 和产物 |

模型是独立实例，Agent 不创建、关闭或重置它。多个 Agent 可以连接同一个模型。
Scoreboard 仍是独立检查组件；常规响应链由 Agent 在内部完成接线，直接检查或特殊
关联仍可单独使用 Scoreboard。

## 项目如何定义一个 Agent

完整可运行的
[AccumulatorAgent](https://github.com/Makiras/xreactor/blob/main/examples/transactions/reference_model.py)
把 Driver、响应采样与匹配配置封装在构造函数中：

```python
class AccumulatorAgent(Agent[Input]):
    def __init__(self, dut, *, response_timeout_cycles):
        monitor = SamplingMonitor(
            PythonPredicateTrigger(
                "response-present", lambda: dut.output is not None,
                sample=RisingEdge(dut.clock), mode="each_sample",
            ),
            capture=lambda event: dut.output,
        )
        super().__init__(
            "accumulator", driver=Input(dut),
            monitors={"response": monitor}, response_monitor="response",
            clock=dut.clock, response_timeout_cycles=response_timeout_cycles,
            request_key=lambda request: request.tag,
            response_key=lambda response: response.tag,
        )
```

这里响应存在的判断是这个示例 DUT 的规则。框架不会推断 ready/valid、reset/flush、
接受条件或合理延迟。省略 key 配置就是 FIFO 匹配；`latency_cycles` 和 `sampled`
沿用 Scoreboard 的固定延迟与逐周期采样语义。`compare` 可指定项目比较函数，
`capacity` 限制检查器的活跃事务和待关联观测。

## send、submit 与 Driver 类型

| Agent 配置 | send(request) | submit(request) |
| --- | --- | --- |
| SyncDriver，无响应检查 | 在调用方 await 时驱动，返回接受 XEvent | 不支持 |
| SyncDriver，有响应检查 | 仍在调用方驱动，接受后由检查器继续跟踪响应 | 不支持 |
| AsyncDriver，无响应检查 | 立即提交输入，返回原 Driver 的接受句柄 | 不支持，需要配置响应检查 |
| AsyncDriver，有响应检查 | 立即提交，返回等待接受的视图；响应仍持续检查 | 立即提交，返回等待核对后响应的 XTransfer |
| 无 Driver 的被动 Agent | 不支持 | 不支持 |

Sync/AsyncSingleCycleDriver 分别沿用对应父类的行为，时序和 idle 策略不变。
Agent 不把 SyncDriver 转换成后台 Driver，也不为它增加 submit。串行异步输入继续使用
原 Driver worker，不新增驱动任务或调度器。

只关心何时接受，可以使用：

```python
event = await agent.send(command)
await agent.finish(timeout_cycles=20)  # 已配置响应检查时，仍须等响应并检查
```

需要响应或多个 outstanding 时，使用已封装 AsyncDriver 的 Agent：

```python
transfers = [agent.submit(command) for command in commands]
await agent.drain(timeout_cycles=30)
# drain 允许后续批次继续提交；Monitor 持续工作。
more = agent.submit(next_command)
await agent.finish(timeout_cycles=20)
result = await more
```

配置响应检查后，send 只改变调用方等待的终点，不取消后台响应检查。它的接受视图
可 await、可在接受前 `cancel()`，不承诺提供完整 XTransfer 的字段；需要完整状态时
用 submit 返回的 XTransfer。即使无人 await，响应失败仍从 finish 或 Execution 退出传播。
输入已经接受后拒绝撤销；取消等待接受/响应的协程不撤销 AsyncDriver 中的底层事务。
SyncDriver 的 send 本来就在调用方执行，取消该调用会中断尚未完成的输入驱动。

有响应检查时，Driver 与检查器的 outstanding 容量持续占用至响应结束。仅需输入接受且
没有待检查响应的接口，构造 Agent 时不配置 `response_monitor`。

## 模型按接受事件推进

模型是普通 Python 对象，无需继承框架基类：

```python
class AccumulatorModel:
    def __init__(self):
        self.total = 0

    def accept(self, command):
        self.total += command.operand
        return Result(command.tag, self.total)
```

当前连接路径使用 Driver 的明确接受事件作为可信输入。AsyncDriver 从接受流取得记录；
SyncDriver 在 send 返回接受事件后交付记录。内部适配层按接受通知顺序调用
`ref.accept(request)`，保存期望，再交给 Scoreboard 关联实际响应。

- 提交时不修改模型，未接受请求撤销后不调用模型。
- 有状态模型按接受顺序推进，不按提交顺序或响应返回顺序推进。
- 每笔期望必须是独立快照；乱序返回时按 key 找到相应期望。
- `agent.submit(request, expected=value)` 或 `agent.send(..., expected=value)`
  覆盖本笔比较值，**仍然调用模型，推进其状态**。没有连接模型时，也可以直接传 expected。
- 模型异常通过事务、finish 或 Execution 退出传播，已观察的同一个后台失败不重复报告。

模型入口必须同步；async 方法及 awaitable 返回值会明确报错。模型应根据规格和有效输入
计算，不能读取被核对的 DUT 输出构造预期。异步远程模型需要项目明确外部同步与预算。

独立使用 `Scoreboard(expected=...)` 时，那个回调仍是“缺少期望时才调用”的预测源。
Agent 的模型更新已从这个条件中分离，二者的覆盖语义不要混用。

## 生命周期与独占消费

`Execution(backend, agents=[...])` 保存实例列表，按注册顺序启动、反向关闭。每个 Agent
先启动 Monitor，再取得 Driver ownership，最后启动响应检查。结束时先检查并停止
Scoreboard，再关闭 Driver 和 Monitor，之后释放 Execution 的任务、watcher 和 backend
lease。启动中途失败也会回收已启动部分，不启动后续 Agent。

`finish()` 禁止继续提交、排空事务、完成指定观察窗口并检查余额；它不关闭 Agent 或
Monitor。退出 Execution 时会再次检查并清理，不隐式无限排空。已完成最后一笔事务后，
Monitor 仍持续观察，因此额外响应不会因提前关闭而被吞掉。

组件必须是尚未启动、由该 Agent 独占拥有的实例。重复注册 Agent、跨 Agent 共享同一个
Driver/Monitor 对象会报错。关闭后的 Agent 和内置 Monitor 都不能重启；下一次运行新建。
Driver 若在自身初始化中失败，仍须释放尚未成功交付给 Agent 的资源。

响应 Monitor 由内部 Scoreboard 独占消费，`agent.recv(response_name)` 会拒绝第二条消费
路径。其他 Monitor 可通过 `await agent.recv(name)` 接收记录。`driver` 和 `monitors`
属性供组件检查；运行中不要绕过 Agent 提交受检查事务或消费它已占用的响应流。

Agent 没有公共 bind、async context 或独立关闭接口，避免由普通用例抢先关闭环境组件。
正常退出、异常、外部取消都会清理组件，保留原始异常和独立清理错误。宿主外部任务不受
影响。并发调用 SyncDriver.send 的任务仍归其调用方所有，环境退出前应等待或取消它们。

## 其他输入来源与多接口

省略 Driver 可建立被动 Agent，通过具名 Monitor 观察接口。当前 `connect(ref)` 的自动
响应链要求主动 Driver 接受事件，不会把纯被动观测伪装成主动事务。项目若需检查实际输入
引脚或被动总线流，应消费输入 Monitor，在自己的明确关联逻辑中调用模型及
`Scoreboard.check()`；不能再让 Driver 接受通知对同一笔输入更新模型一次。

共享模型不会自动取得跨接口硬件顺序。项目需明确定义同 tick 冲突、仲裁及状态更新规则，
不能以 asyncio 回调顺序代替硬件仲裁。独立、可交换的操作可以直接连接；有顺序依赖时
由项目在输入汇合层落实规格。模型的 reset/flush、退休或提交事件也由项目定义。

当前自动响应链是一笔接受请求对应一笔响应。零个、多个或自主输出需要先定义对应关联
规则；None 本身是合法期望值，不表示“没有响应”。

## 运行示例

```bash
python3 -m examples.transactions.reference_model
python3 -m examples.transactions.reference_model --backend native
```

输入 3、5、7 的期望分别为 3、8、15，实际响应顺序为 tag 2、1、3。DUT 替身与参考模型
不共享状态或预测函数；模型能脱离 Execution 单独测试。回归覆盖同步/异步输入、多批次、
未接受取消、等待取消、显式期望覆盖、模型异常、共享模型及失败清理。
覆盖率与运行产物见[完整运行配方](verification-flow.md)。
