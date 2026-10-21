# XReactor 用户手册

XReactor 是基于 asyncio 的 Python 硬件验证框架。Driver 施加输入，Monitor 捕获输出，
Agent 封装接口并连接参考模型，Scoreboard 核对事务；Execution 管理仿真推进与组件生命周期。

## 第一次写验证测试

从[零基础学习路线](getting-started/index.md)开始。无需先学会 asyncio 或搭建 RTL
仿真器：先用四行 Python 得到第一次 PASS 和 FAIL，再用一个小累加器练习输入、
时钟、空闲和溢出。后续每一步都从已有代码出发，说明为什么需要下一个组件。

- [安装环境](getting-started/installation.md) → [第一次断言](getting-started/first-test.md)
  → [输入与时钟](getting-started/clock-and-input.md) → [多组边界](getting-started/more-cases.md)。
- [Driver](getting-started/driver.md) → [Monitor](getting-started/monitor.md)
  → [Agent](getting-started/agent-test.md) → [模型与 fixture](getting-started/reference-model.md)。
- [流水与多批次](getting-started/pipeline.md) → [四种失败练习](getting-started/failures.md)
  → [覆盖率报告](getting-started/coverage.md)。

主线集中在一个测试文件和一个 DUT 支持文件；每步展示新增代码、运行命令、预期输出和
动手修改练习。已有经验、只想查某个功能时，
再使用下方指南。

已有 Toffee 验证环境，准备迁移到采用 xreactor 实现的 Toffee v2 时，阅读
[Toffee v2 迁移说明](guides/toffee-v2-migration.md)，核对 API 对应关系、采样和模型更新语义。

## 按使用场景找功能

| 要完成的工作 | 功能指南 |
| --- | --- |
| 用统一 CLI 启动多 worker，自动汇总覆盖率并回收中间产物 | [统一 CLI 与多 worker](guides/runner.md) |
| 启动/退出仿真、复用 backend、配置推进预算 | [Execution 与 Backend](guides/execution-and-backend.md) |
| 等待时钟、信号条件、超时或多个事件 | [Trigger](guides/triggers.md) |
| 在 native 引擎识别条件、时间窗口和状态机 | [编译条件、Sequence、FSM](guides/compiled-triggers.md) |
| 持续处理事件，并保留采样时刻的数据 | [订阅](guides/subscriptions.md) |
| 绑定信号、保存结构快照、拆分 packed bus | [数据](guides/data.md) |
| 单周期或多周期输入、排队、流水阶段资源占用 | [Driver](guides/drivers.md) |
| 明确采样条件、有效响应、被动观察 | [Monitor](guides/monitors.md) |
| 封装项目接口，连接有状态或共享参考模型 | [Agent 与参考模型](guides/agents-and-reference-models.md) |
| 直接比较、FIFO/key/固定延迟关联、取消与结束检查 | [Scoreboard](guides/scoreboard.md) |
| 使用项目明确选择的 ready/valid 协议 | [可选协议视图](guides/protocols.md) |
| 定义 bin、门控和 cross，保存/合并覆盖率 | [Functional coverage](guides/coverage.md) |
| 从功能点设计场景，合并各 case 的实际覆盖 | [功能点与跨用例覆盖](guides/functional-points.md) |
| 管理预算、seed、产物目录，失败时仍保存报告 | [运行配方](guides/verification-flow.md) |
| 接入 HTTP/异步库，显式同步外部结果 | [外部 asyncio 集成](guides/asyncio-and-pytest.md) |

需要端到端代码时，查看[流水输入场景](guides/pipeline-examples.md)和[可运行示例](examples.md)。

## 查接口与验证边界

[API 索引](reference/api.md)列出全部顶层导出；[功能与回归索引](reference/feature-map.md)
把指南、示例和行为回归连接起来。[行为与限制](reference/semantics-and-limitations.md)说明
单时钟、采样、事务、外部异步任务和覆盖率格式的边界。测试通过不代表任意 DUT 的协议
假设都适用，项目仍须明确接受条件、初始化和预算。
