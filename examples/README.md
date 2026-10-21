# 可运行示例

第一次使用，先看[入门教程](../docs/getting-started/index.md)。
以下命令均在仓库根目录执行；先按 [README](../README.md) 安装测试依赖。

## 先运行纯 Python 例子

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_addition
python3 -m pytest -q examples/getting_started/test_accumulator.py
```

教程从断言开始，逐步加入输入、时钟、Driver、Monitor、Agent 和覆盖率。
故意失败的练习在 `failure_lab.py`，需要按教程单独选择运行。

## 按要解决的问题选择

| 问题 | 示例 |
| --- | --- |
| 等待时钟和信号 | [基本等待](triggers/basic_execution.py) |
| 持续订阅事件 | [订阅](triggers/subscription.py) |
| 直接比较和流水线匹配 | [Scoreboard](transactions/scoreboard.py) |
| 连接有状态参考模型 | [参考模型](transactions/reference_model.py) |
| 连续输入、多周期重叠和乱序响应 | [流水线](transactions/pipeline.py) |
| 手动定义覆盖点和交叉覆盖 | [功能覆盖率](coverage/functional_coverage.py) |
| 用类定义覆盖模型 | [类声明](coverage/declarative_pipeline.py) |
| 按编号统计请求响应和延迟 | [事务覆盖](coverage/declarative_protocol.py) |
| 统计 Sequence 和 FSM 的完成事件 | [时序覆盖](coverage/trigger_bins.py) |

例如：

```bash
python3 examples/triggers/basic_execution.py
python3 -m examples.transactions.reference_model
python3 examples/transactions/pipeline.py --backend memory
python3 -m examples.coverage.declarative_pipeline --backend manual
```

## 换用原生后端

先运行 `python3 examples/getting_started/check_native.py` 确认绑定可用，再执行：

```bash
python3 examples/transactions/pipeline.py --backend native
python3 -m examples.coverage.trigger_bins --engine xcomm --strategy native
```

这些例子使用原生时钟和信号，但计算行为仍由 Python 替身实现。
真实 DUT 的构建、端口绑定和初始化分别见 [Cache](integration/cache/README.md)
和 [e203](integration/e203/README.md)，它们需要额外的项目依赖。

[用户手册中的示例索引](../docs/examples.md)还列出了故障练习和报告用法。
