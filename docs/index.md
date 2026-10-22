# Toffee 用户手册

**本文档对应 `main` 分支的 Toffee 2。Toffee Legacy（旧版）位于
[`master` 分支](https://github.com/XS-MLVP/toffee/tree/master)。**
Toffee 2 由 XReactor 迁入，与 Legacy 的 API 不兼容。已有项目请先阅读
[迁移指南](guides/toffee-v2-migration.md)，根据所用版本选择文档和安装方式。

写硬件测试，通常要做三件事：给设计送输入、等待结果、判断结果是否正确。
Toffee 帮你把这些工作写成 Python 测试，并在需要时拆成可复用的组件。

如果第一次接触验证框架，从[入门教程](getting-started/index.md)开始。
你会用同一个累加器逐步练习，不必先学完 asyncio，也不必先搭好 RTL 仿真环境。

## 从第一个测试开始

1. [准备环境](getting-started/installation.md)，运行[第一条断言](getting-started/first-test.md)。
2. 给 DUT [送输入、等时钟](getting-started/clock-and-input.md)，再测[多组数据和边界](getting-started/more-cases.md)。
3. 把重复代码整理成 [Driver](getting-started/driver.md)、[Monitor](getting-started/monitor.md) 和 [Agent](getting-started/agent-test.md)。
4. 接入[参考模型](getting-started/reference-model.md)，检查[流水线和多批次输入](getting-started/pipeline.md)。
5. 练习[定位失败](getting-started/failures.md)，用[覆盖率](getting-started/coverage.md)找出还没测到的情况。

教程中的代码就在仓库里。每页都给出要运行的命令和对应测试，不需要把所有片段重新拼一遍。

## 已经有项目，想查具体用法？

| 你要做什么 | 阅读哪一页 |
| --- | --- |
| 配置和启动仿真 | [Execution 与 Backend](guides/execution-and-backend.md) |
| 等待时钟、条件或超时 | [Trigger](guides/triggers.md) |
| 识别跨周期的条件和状态变化 | [Sequence 与 FSM](guides/compiled-triggers.md) |
| 持续接收事件，在采样时保存数据 | [订阅](guides/subscriptions.md) |
| 把一组信号绑定到 Python 对象 | [信号与 Bundle](guides/data.md) |
| 编写单周期、多周期或可重叠的输入驱动 | [Driver](guides/drivers.md) |
| 收集输出，不漏掉只出现一拍的结果 | [Monitor](guides/monitors.md) |
| 组合驱动、监视器和参考模型 | [Agent 与参考模型](guides/agents-and-reference-models.md) |
| 比较结果，按顺序或编号匹配请求与响应 | [Scoreboard](guides/scoreboard.md) |
| 定义场景和交叉覆盖，找到漏测情况 | [功能覆盖率](guides/coverage.md) |
| 用类和类型注解组织覆盖模型 | [类声明](guides/coverage-classes.md) |
| 统计“请求后几拍内收到响应”等事件 | [时序覆盖](guides/coverage-patterns.md) |
| 按事务编号统计延迟和乱序 | [事务覆盖](guides/coverage-transactions.md) |
| 运行多进程回归、查看汇总报告 | [运行测试与汇总覆盖率](guides/runner.md) |
| 从 Toffee 验证环境迁移 | [迁移指南](guides/toffee-v2-migration.md) |

完整代码见[示例索引](examples.md)。查参数与接口时，使用 [API 索引](reference/api.md)。
开始接入真实 DUT 前，先确认[采样、关闭和并发使用的限制](reference/semantics-and-limitations.md)。
