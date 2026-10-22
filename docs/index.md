# Toffee 用户手册

本文档对应从 XReactor 迁入的 Toffee 2；Legacy 保留在
[`master`](https://github.com/XS-MLVP/toffee/tree/master)，升级见[迁移指南](guides/toffee-v2-migration.md)。

写硬件测试，通常要做三件事：给设计送输入、等待结果、判断结果是否正确。
Toffee 帮你把这些工作写成 Python 测试，并在需要时拆成可复用的组件。

按你当前的目的选择入口，各部分可以独立阅读。

| 你的目的 | 从这里开始 | 读完能做什么 |
| --- | --- | --- |
| 第一次使用，想跟着做 | [教程](getting-started/index.md) | 用一个 8 位累加器完成断言、驱动、自动检查和覆盖率练习 |
| 已经有测试，要完成具体任务 | [操作指南](guides/index.md) | 按任务接入 DUT、组织验证组件、运行回归或迁移项目 |
| 查一个接口或参数 | [参考](reference/index.md) | 找到 API、默认值、命令行选项和支持边界 |
| 理解行为背后的原因 | [原理说明](explanation/index.md) | 理解时钟推进、任务归属，以及检查和覆盖率的关系 |

## 第一次写测试

从[准备环境](getting-started/installation.md)和[第一次 PASS 与 FAIL](getting-started/first-test.md)开始。
教程前半部分只需 Python，不需要先搭好 RTL 仿真环境。每页都有仓库中的测试文件、
运行命令和预期结果；完成一项练习后再引入下一个概念。

## 常用任务

- [配置和启动仿真](guides/execution-and-backend.md)：创建 Backend，并在测试结束时释放资源。
- [编写 Driver](guides/drivers.md)和[采集输出](guides/monitors.md)：把重复的输入输出时序放进组件。
- [接入参考模型](guides/agents-and-reference-models.md)：在输入接受后推进模型，自动比较响应。
- [运行回归与汇总覆盖率](guides/runner.md)：选择测试、并行运行和排查收集失败。

完整代码见[可运行示例](examples.md)；查询功能对应的实现和回归，见[功能索引](reference/feature-map.md)。
