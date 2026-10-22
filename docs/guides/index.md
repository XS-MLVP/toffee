# 操作指南

这里按项目任务组织步骤，适合已经能运行一条测试的读者。第一次使用请从
[累加器教程](../getting-started/index.md)开始；查默认值和限制请看[参考](../reference/index.md)。

## 接入与运行仿真

| 任务 | 指南 |
| --- | --- |
| 创建 Backend、启动测试、复用 fixture | [配置 Execution 与 Backend](execution-and-backend.md) |
| 等待时钟、信号、超时或外部事件 | [使用 Trigger](triggers.md) |
| 识别跨周期条件与状态变化 | [编译条件、Sequence 与 FSM](compiled-triggers.md) |
| 持续接收事件并保存采样快照 | [持久订阅](subscriptions.md) |
| 接入 HTTP、Queue 或后台服务 | [集成 asyncio 与 pytest](asyncio-and-pytest.md) |

## 组织验证组件

| 任务 | 指南 |
| --- | --- |
| 把 DUT 信号绑定到结构化对象 | [信号与 Bundle](data.md) |
| 实现单周期、多周期或可重叠的输入 | [编写 Driver](drivers.md) |
| 收集输出，避免漏掉单拍结果 | [编写 Monitor](monitors.md) |
| 组合组件并接入有状态参考模型 | [Agent 与参考模型](agents-and-reference-models.md) |
| 比较结果，按顺序、编号或固定延迟关联事务 | [使用 Scoreboard](scoreboard.md) |
| 使用 ready/valid 通道的现成视图 | [协议视图](protocols.md) |
| 运行连续输入和阶段重叠场景 | [流水输入示例](pipeline-examples.md) |

## 覆盖率与项目回归

| 任务 | 指南 |
| --- | --- |
| 定义分桶、交叉和覆盖目标 | [功能覆盖率](coverage.md) |
| 用类复用覆盖模型 | [覆盖模型类声明](coverage-classes.md) |
| 统计请求响应延迟与乱序 | [事务覆盖](coverage-transactions.md) |
| 统计跨周期事件 | [时序覆盖](coverage-patterns.md) |
| 将验证要求变成可观测场景 | [功能点与跨用例覆盖](functional-points.md) |
| 将组件、检查和覆盖采样接入同一环境 | [组合验证环境](verification-flow.md) |
| 运行多进程回归，查看报告并定位失败 | [运行测试与汇总覆盖率](runner.md) |

## 迁移与维护

- [迁移到 Toffee 2](toffee-v2-migration.md)：分别处理 XReactor 和 Toffee Legacy 项目。
- [版本、CI 与发布](versioning-and-releases.md)：为框架变更选择版本，检查发布结果。

需要完整代码时，按[示例索引](../examples.md)选择可运行的项目。
