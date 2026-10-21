# 组件组合、覆盖率与运行产物

这个项目配方将 Driver、Monitor、Scoreboard 和覆盖率接在一起；普通用例只提交事务和
调用有预算的 finish。环境构造 Agent 实例并连接独立模型，Execution 统一管理组件，
Agent 内部连接响应 Scoreboard。组件边界与有状态模型接入见[Agent 与 reference model](agents-and-reference-models.md)。

可运行源码：
[组合实现](https://github.com/Makiras/xreactor/blob/main/examples/transactions/verification_flow.py)、
[pytest 用例及 fixture](https://github.com/Makiras/xreactor/blob/main/examples/transactions/test_pipeline_coverage.py)。

```bash
python3 -m pytest -q -s examples/transactions/test_pipeline_coverage.py
```

fixture 使用 pytest 的每用例临时目录，再按 seed 与运行序号分目录，打印产物路径。
配方拒绝覆写已有运行目录。每次运行保存 functional.json、coverage.html 和 run.json；
后者记录 nodeid、seed、backend、Python 版本、预算、最后 tick、运行状态及异常。
示例的两个 case 分别贡献 tag 1/2 和 tag 2/3；各自为 2/3，合并为 3/3。
[跨用例覆盖](functional-points.md)说明如何合并这两份产物；测试执行结果使用 pytest 原生报告。

## 生命周期的唯一所有者

| 对象 | 创建与关闭者 | 结束动作 |
| --- | --- | --- |
| DUT 替身、Backend | 项目运行配方 | 组件与 Execution 退出后 close |
| Execution | 项目 context | 释放推进任务、watcher 与 backend lease |
| Driver | Execution 内的 Agent | 清理输入任务、释放信号 ownership |
| Monitor | Agent；内部 Scoreboard 独占消费 | 随 Agent 启动和关闭 |
| Reference model | 项目环境 | Agent 仅调用 accept，不重置或关闭共享模型 |
| 覆盖率数据库 | 项目运行配方 | 在 finally 中保存，包括检查失败和取消 |

Execution 接收 `agents=[agent]`，统一启动并反向清理组件；Agent 本身不是 context
manager。启动过程中出错也会清理已经启动的组件。DUT 构造函数内部若分步取得资源，应自己处理未完成构造时的释放。
本例沿用确定性 ToyPipeline 替身，不声称验证真实 RTL 的初始化、reset 或 flush。

## 同一条观测只采样一次

`CoveredMonitor` 是项目内的小适配器；它代理唯一的 recv，在返回前采样：

```python
async def recv(self):
    observation = await self.source.recv()
    self.coverage.sample(
        {"tag": observation.value.tag},
        metadata={"tick": observation.event.tick},
    )
    return observation
```

Scoreboard 收到的仍是同一个不可变 Transfer。适配器没有自己的队列或后台任务，
没有第二个消费者。协议无关示例复用 ToyPipeline 的事务替身；真实信号采样可把 source
换成 `SamplingMonitor(output_event, capture=...)` 或项目协议 Monitor。

本例选择“交付检查器之前的 DUT 观测”作为覆盖率采样点。一个响应即使随后比较失败，
该观察仍被计数；已停止检查后未交付的观测不会计数。因此这些计数不等于“检查通过的
事务数”，也不保证涵盖故障后所有 DUT 活动。若要统计输入接受或核对成功，应另选明确
的采样位置，而不是再从同一 Monitor 调用 recv。

pytest 执行结果、functional bins 和 simulator line coverage 分别保留。示例只采集
functional bins；真实 simulator 的 LCOV 导出仍由项目工具负责。兼容 schema 的数据库
可用 `CoverageDatabase.merge()` 合并，不兼容 schema 会报错。

## 三种预算分别设置

```python
async with pipeline(seed=17) as agent:
    transfers = [agent.submit(value) for value in (1, 2, 3)]
    await agent.finish(timeout_cycles=30, observe_cycles=1)
    results = [await transfer for transfer in transfers]
```

运行配方显式配置单事务响应周期预算和整体墙钟时限；用例自己传入 finish 周期预算及
尾部观察窗口。`Execution.max_settle_rounds` 仅限制阶段内即时工作不收敛，不能替代这两种
超时。墙钟限制通过配方内部的 asyncio.timeout 实现，用例不需要操作 asyncio 原语。

seed 由用例的局部 `random.Random(seed)` 使用，不修改全局随机状态。运行配方记录 seed，
不推断 DUT 的初始化方式，也不管理 seed 矩阵。run.json 的 state 描述这个仿真上下文；
上下文以外的 pytest 断言和其他 fixture teardown 结果，以 pytest 原生结果为准。

检查失败、外部取消和清理失败都会执行产物保存。导出失败与已有验证异常一起保留，
不会把失败改成成功。对无法写入的目录，不能保证有产物，异常会明确指出保存失败。

MemoryBackend 和原生 XClock 的回归覆盖正常、比较失败、测试体异常、外部取消、
启动失败及清理失败，并检查 watcher、backend lease、信号空闲状态和任务释放。
HTML 导出失败也有独立回归。此配方不承担 pytest-xdist/rerun 汇总或仿真器进程管理。
