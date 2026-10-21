# 示例

以下命令从仓库根目录执行，先安装 `python3 -m pip install -e '.[test]'`。

## 从零开始的累加器教程

[学习路线](getting-started/index.md) 从四行断言开始，逐步加入时钟、边界用例、Driver、
Monitor、Agent、参考模型、流水提交、故障定位和覆盖率。完整主线代码随仓库提供，每步讲解新增代码、运行结果
和动手练习；主线集中在 `test_accumulator.py`，由 `toy_dut.py` 提供教学 DUT，
不要求原生绑定 或 RTL。

```bash
python3 -m pytest -q examples/getting_started
```

仓库原始示例预期 `16 passed`。单页命令和每个文件的用途见教程。
`failure_lab.py` 是需要显式运行的四种故障练习，`native_lab.py` 是另需 xspcomm 的选读，
两者不会被以上目录命令自动收集。

## Trigger

[basic_execution.py](https://github.com/Makiras/xreactor/blob/main/examples/triggers/basic_execution.py) 演示：

- `Execution`；
- `RisingEdge` 和 `FallingEdge`；
- `Value`；
- `@xtrigger`。

```bash
PYTHONPATH=src python3 examples/triggers/basic_execution.py
```

## 采样与持续订阅

[subscription.py](https://github.com/Makiras/xreactor/blob/main/examples/triggers/subscription.py)
示范在采样时 capture 不可变数据，再在 async handler 中处理：

```bash
python3 examples/triggers/subscription.py
```

## 事务检查

[scoreboard.py](https://github.com/Makiras/xreactor/blob/main/examples/transactions/scoreboard.py)
演示协议无关的 Driver/Monitor、固定延迟响应和带预算的 finish：

```bash
python3 examples/transactions/scoreboard.py
```

预期输出 `checked=8, passed=8, completed=8`。以上入门、原生检查、订阅和事务
示例均在 CI 中执行。

## 流水输入、阶段重叠与乱序响应

[完整示例文档](guides/pipeline-examples.md) 对比连续发射、串行/重叠的多阶段输入、
只等待接受的非阻塞提交、逐笔 send 对照、FIFO/固定延迟/key 关联，并提供四种可复现的失败场景。

```bash
python3 examples/transactions/pipeline.py
python3 examples/transactions/pipeline.py --backend native
python3 -m pytest -q tests/integration/test_pipeline_examples.py --require-xspcomm
```

默认命令不依赖 RTL 或原生绑定；第二条需要 xspcomm。CI 在两种 backend 上
运行成功场景，并通过 pytest 验证预期失败、实际采样时序和清理。

## Agent 与参考模型

有状态参考模型、Agent 与乱序响应的完整演示见
[Agent 与 reference model](guides/agents-and-reference-models.md)：

```bash
python3 -m examples.transactions.reference_model
python3 -m examples.transactions.reference_model --backend native
```

完整的 Driver → Monitor → Scoreboard → 覆盖率 → pytest 产物配方，见
[组件组合与运行产物](guides/verification-flow.md)。它包括失败时保存及 seed 隔离：

```bash
python3 -m pytest -q -s examples/transactions/test_pipeline_coverage.py
```

## 功能覆盖率

[functional_coverage.py](https://github.com/Makiras/xreactor/blob/main/examples/coverage/functional_coverage.py) 演示 coverpoint、bin、
cross、JSON 输出和覆盖率检查。

```bash
python3 examples/coverage/functional_coverage.py --output output/functional-coverage.json
```

按需要继续选择：

- [类声明](guides/coverage-classes.md)：复用覆盖点和模型，支持手动采样与信号绑定。
- [事务覆盖](guides/coverage-transactions.md)：按请求编号统计延迟和乱序。
- [时序覆盖](guides/coverage-patterns.md)：统计 Sequence 或 FSM 的完成事件。

## 从教学替身到真实 DUT

完成入门后按目标选一个环境即可，无需先运行所有示例。真实 DUT 需要额外的 Picker、
Verilator 和原生绑定，不加入普通教程的默认测试收集。

| 目标 | 入口 | 关注的框架功能 |
| --- | --- | --- |
| 将输入变为多条下游命令，检查路由/地址/数据/错误 | [e203 构建与使用](https://github.com/Makiras/xreactor/blob/main/examples/integration/e203/README.md) | Bundle 快照、接受后更新模型、Scoreboard 上下文、coverage、pytest 执行结果、LCOV/HTML、失败产物 |
| 验证 refill、读写 mask、replacement、MMIO 和 probe/release | [Cache 构建与使用](https://github.com/Makiras/xreactor/blob/main/examples/integration/cache/README.md) | 项目 SyncDriver、Monitor、CoherenceAgent 多拍组装、Scoreboard、参考内存、coverage 与后台清理 |
| 验证 submit、流水、乱序、取消及截止周期 | [流水场景](guides/pipeline-examples.md)、[Agent/模型](guides/agents-and-reference-models.md) | AsyncDriver、SingleCycle 类、Agent、FIFO/key/固定延迟、finish，支持 memory/原生两种后端 |

e203 和 Cache 的 README 都从构建、只跑定向、增加随机 seed，到查看报告逐步展开。
多种 Driver 各有适用场景，不为了展示 API 强行改变真实 DUT 的驱动时序。
Cache 的独立 overlap 探针目前仍预期失败；功能 campaign 通过不代表该已知问题修复。
