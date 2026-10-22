# 仿真调度与资源归属

Toffee 中有两种不同的进度：Python 任务在 asyncio 上运行，DUT 在仿真时钟上前进。
理解两者的关系，可以解释为什么等待 HTTP 时 DUT 有时继续运行，以及为什么退出测试
不会自动销毁 DUT。

## Python 就绪工作与时钟推进

asyncio 是唯一执行 Python Task 和回调的调度器。`Execution` 观察属于本次仿真的
即时回调；它们和继续唤醒的工作处理完后，才允许下一次时钟推进或 `DriveStable` 采样。
这个过程称为阶段收敛。例如，一个响应唤醒检查器，检查器释放输入容量，Driver 又提交
下一笔输入：这条即时工作链应先处理完，时钟才能前进。

Backend 执行推进并返回命中的事件。支持的编译条件可以在原生后端求值，减少未命中
周期中的 Python 调度。需要每拍执行 Python 的 Driver、Monitor 或 `@pytrigger`，
仍会产生相应的调用开销，因此批量推进的收益取决于实际测试结构。

等待一个尚未完成的外部 Future，不代表还有就绪的仿真工作。只要有 DUT Trigger 或
持续订阅，时钟就可以继续推进；没有仿真 watcher 时，仅等待 HTTP 或 Queue 不会推进时间。
持续 Monitor 通常会保留 watcher，因此“测试在等配置”并不等于“DUT 已暂停”。

`Execution.paused()` 明确冻结仿真时间，同时让宿主 asyncio 继续工作。
暂停期间等待必须靠仿真推进才能满足的 Trigger，会使等待无法完成。
具体接入方式见[外部 asyncio 集成](../guides/asyncio-and-pytest.md)。

## 三种预算限制不同的工作

| 预算 | 限制什么 | 不能证明什么 |
| --- | --- | --- |
| 事务或 `finish` 的周期预算 | DUT 响应、排空和尾部观察占用的仿真周期 | 宿主程序会在指定秒数内结束 |
| 墙钟超时 | 实际经过的时间，包括外部 I/O | DUT 满足指定的周期延迟 |
| `max_settle_rounds` | 单个阶段内交回 asyncio 的收敛轮数 | 已经等待了多少个仿真周期 |

域内任务无限循环 `await asyncio.sleep(0)` 时，当前阶段一直有就绪工作。
`SimulationNotSettledError` 报告这种不收敛，时钟不会跳过这些工作强行前进。
增加预算只适合有限但很长的任务链；后台服务的归属问题需要通过
`external_task` 处理。参数定义见[Execution 参考](../reference/execution.md)。

## 调度归属与生命周期归属

任务属于本次仿真的调度范围，并不意味着它自动交给 Execution 管理生死。
Execution 启停显式注册的 Agent，Agent 管理自己的 Driver 和 Monitor；
用户独立创建的后台任务仍由创建者结束。`external_task` 清除隐式 Reactor 绑定，
使服务运行在仿真调度范围之外，但不会代替调用方清理该服务。

| 资源 | 生命周期负责人 |
| --- | --- |
| 当前测试的等待、订阅、编译状态和推进任务 | `Execution` |
| 注册给 Execution 的 Agent 及其组件 | `Execution` 与 Agent 的组件管理逻辑 |
| Backend | 创建它的代码或 fixture，负责 `close()` |
| DUT | 创建它的项目代码或 fixture，负责初始化和释放 |
| 独立外部 Task | 创建者，负责等待完成或取消并回收 |

这个边界允许多个测试先后复用 Backend，却不隐式复位 DUT。需要独立初态时，
项目必须定义 reset sequence；不能把“上次 Execution 已退出”当作“设计已回到初态”。

## 一个时钟只能有一个推进者

`XCommClockBackend` 在 Execution 内取得对应 XClock 的独占控制权。
如果测试又调用 `dut.Step()`，两套推进逻辑会失去统一的采样顺序，因此该调用被拒绝；
暂停也不会释放这份控制权。退出后才恢复手动推进。

独占只覆盖所用的 clock domain；多个 Execution 没有自动合成一个共同时间轴。
精确的手动步进限制见[Execution 参考](../reference/execution.md)，
fixture 的写法见[配置 Execution 与 Backend](../guides/execution-and-backend.md)。
