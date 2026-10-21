# 功能、场景与回归索引

本页按使用需求连接功能入口、可运行示例与回归。测试源码链接指向相应功能组；表格
描述它们实际核对的行为，不用“文件存在”或测试总数代替功能验证。

| 功能 | 使用场景与指南 | 示例 / 对应测试 |
| --- | --- | --- |
| 入门运行 | 从 assert、时钟和边界逐步到组件、模型、流水、故障和覆盖率 | [零基础教程](../getting-started/index.md)；examples/getting_started 的正常示例进入默认回归，test_quickstart_tutorial 检查文档片段一致性与故障练习退出结果；native_lab 显式运行 |
| 仿真生命周期 | [Execution](../guides/execution-and-backend.md)：后端复用、显式暂停、独占 lease、阶段收敛、退出清理 | [runtime](https://github.com/Makiras/xreactor/blob/main/tests/runtime/test_runtime.py)、[observer](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_asyncio_observer.py) 检查顺序、取消、外部任务隔离和不收敛失败 |
| 时钟与条件 | [Trigger](../guides/triggers.md)：rising/falling/drive stable、默认采样、enter/change/each_sample、ValueChange、周期/墙钟超时 | [basic_execution](https://github.com/Makiras/xreactor/blob/main/examples/triggers/basic_execution.py)；[native 回归](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_xcomm_backend.py) 检查真实 XClock、宽信号、X/Z、注册基线及采样 phase |
| 事件组合与外部输入 | AnyOf/AllOf、Queue/Event/TaskComplete、HTTP 和域外任务的[显式同步](../guides/asyncio-and-pytest.md) | [adapters](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_asyncio_adapters.py)、[HTTP](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_http_integration.py) 检查组合事件、超时与 shield 所有权 |
| 编译观察模式 | [编译条件、Sequence/FSM](../guides/compiled-triggers.md)：条件 编译、窗口/保持/重启、状态分支优先级 | [runtime](https://github.com/Makiras/xreactor/blob/main/tests/runtime/test_runtime.py) 与 [native](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_xcomm_backend.py) 核对匹配、cache 及原生执行；[窗口与保持](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_compiled_patterns.py) 对照边界、超时重启和连续计数中断 |
| 持久订阅 | [同步 capture 与异步 handler](../guides/subscriptions.md)、无注册间隙、lossless/latest、异常 | [subscription 示例](https://github.com/Makiras/xreactor/blob/main/examples/triggers/subscription.py)；[订阅回归](https://github.com/Makiras/xreactor/blob/main/tests/runtime/test_subscriptions.py) 检查快照、顺序、容量和失败清理 |
| 数据与绑定 | [Bundle/Field/packed](../guides/data.md)：快照、位宽、metadata、维度/位序/stride/offset、四态 | [data 回归](https://github.com/Makiras/xreactor/blob/main/tests/interfaces/test_data_interfaces.py) 检查形状、方向和信号身份；原生回归检查 live 子视图、padding 与宽度 |
| Driver | [Driver 模板](../guides/drivers.md)：同步调用/立即提交、单周期/固定阶段多周期、capacity/max_active、资源锁、idle、取消 | [流水示例](../guides/pipeline-examples.md)；[Sync](https://github.com/Makiras/xreactor/blob/main/tests/interfaces/test_sync_driver.py)、[Async](https://github.com/Makiras/xreactor/blob/main/tests/interfaces/test_async_driver.py)、[多周期](https://github.com/Makiras/xreactor/blob/main/tests/interfaces/test_staged_driver.py)、[交接回归](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_driver_handoffs.py) 检查真实接受 tick、阶段重叠、互斥、公平性、无空拍和清理 |
| Monitor | [采样/有效响应/被动观察](../guides/monitors.md)：显式 trigger、不可变记录、容量、关闭、失败 | [采样回归](https://github.com/Makiras/xreactor/blob/main/tests/interfaces/test_sampling_monitor.py)、[共用关闭契约](https://github.com/Makiras/xreactor/blob/main/tests/interfaces/test_monitor_lifecycle.py) 覆盖两类内置 Monitor、两种后端及全部等待者唤醒 |
| Agent 与模型 | [实例封装和 connect](../guides/agents-and-reference-models.md)：两类 Driver、状态更新、共享模型、多批次、唯一消费、统一生命周期 | [有状态示例](https://github.com/Makiras/xreactor/blob/main/examples/transactions/reference_model.py)；[Agent 集成](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_agent_native.py) 和 [清理单测](https://github.com/Makiras/xreactor/blob/main/tests/interfaces/test_agent.py) 检查实际接受、expected 覆盖、无人等待失败及启动回滚 |
| Scoreboard 与事务 | [直接比较和响应检查](../guides/scoreboard.md)：差异、FIFO/key/固定延迟、逐拍窗口、drain/finish、取消 | [scoreboard 示例](https://github.com/Makiras/xreactor/blob/main/examples/transactions/scoreboard.py)；[关联回归](https://github.com/Makiras/xreactor/blob/main/tests/runtime/test_scoreboard.py)、[XTransfer](https://github.com/Makiras/xreactor/blob/main/tests/runtime/test_transfers.py)、[native 截止周期](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_transaction_lifecycle_native.py) 检查漏/迟/重复响应、响应先于接受通知、截止周期优先及活跃表释放 |
| 可选协议工具 | [ReadyValid/Decoupled](../guides/protocols.md)、角色视图、ReadyValidMonitor 与驱动函数 | [protocols](https://github.com/Makiras/xreactor/blob/main/tests/interfaces/test_protocols.py)、data/原生回归保留握手接受、反压、候选采样与 cleanup 语义 |
| 功能覆盖率 | [功能点与跨用例覆盖](../guides/functional-points.md)、[bin/cross/门控/目标](../guides/coverage.md)、illegal、快照字段、原子采样、保存/合并/reset | [coverage 示例](https://github.com/Makiras/xreactor/blob/main/examples/coverage/functional_coverage.py)；[coverage 回归](https://github.com/Makiras/xreactor/blob/main/tests/coverage/test_coverage.py) 检查命中优先级、门控、重叠、交叉限制、计权、schema 及合并 |
| 报告与运行产物 | [运行配方](../guides/verification-flow.md)：seed、预算、单消费者覆盖率、失败导出、JSON/HTML/LCOV | [配方测试](https://github.com/Makiras/xreactor/blob/main/examples/transactions/test_pipeline_coverage.py)；[产物回归](https://github.com/Makiras/xreactor/blob/main/tests/integration/test_verification_flow.py) 与 [报告回归](https://github.com/Makiras/xreactor/blob/main/tests/coverage/test_coverage_report.py) 检查失败保留、计数、转义、目录保护和源码页 |

## 运行对应的回归

仓库根目录安装 test extra 后，`python3 -m pytest -q` 同时执行框架回归、入门教程
及运行产物示例。没有 xspcomm 时原生用例会 skip；发布或完整验收必须使用：

```bash
python3 -m pytest -q --require-xspcomm
mkdocs build --strict
```

只检查某个功能，可传表中的测试文件，例如：

```bash
python3 -m pytest -q tests/interfaces/test_agent.py tests/integration/test_agent_native.py --require-xspcomm
python3 -m pytest -q tests/coverage
```

MemoryBackend 用例核对 Python 语义，原生 XClock 用例核对真实后端采样/调度，两者不能
互相代替。e203/cache 等真实 DUT 的构建与产物依赖不包含在默认回归，见
[项目示例](../examples.md)的环境说明；此表不声称已经验证任意 RTL。

新增功能应同时更新所属指南、最小可运行场景与行为回归；纯文案变更无需复制实现式测试。
完整顶层导出见 [API](api.md)，当前能力边界见[限制](semantics-and-limitations.md)。
