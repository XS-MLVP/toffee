# 选读：原生时钟与项目 DUT

前置：完成主线，并准备好 xcomm/xspcomm Python 绑定。
主线只学习测试组织；这一步确认同一套测试代码可以随原生 XClock 推进。

## 先确认加载的是哪个 绑定

Toffee 不附带 xspcomm。原生后端需要 `XClock.StepHalf`、`GetHalfTick`、`GetPhase`、
`XData` 和 `XTriggerEngine` 等接口。开发使用的提交、包版本、ABI 和协议版本以
[依赖清单](https://github.com/XS-MLVP/toffee/blob/main/.github/native-dependencies.json)为准，
不要只按包名判断版本是否匹配。安装到当前 Python 环境后，先运行：

```bash
python3 examples/getting_started/check_native.py
```

如果只在本地构建目录里，先把**含有 xspcomm 包目录的父目录**加入搜索路径。
下面是占位路径，需要替换为你的实际构建路径：

```bash
export PYTHONPATH="/实际路径/xcomm/python${PYTHONPATH:+:$PYTHONPATH}"
python3 examples/getting_started/check_native.py
```

脚本会打印实际加载的 绑定 文件路径，最后应是：

```text
native clock: tick=2, phase=RISING_STABLE
```

| 现象 | 下一步 |
| --- | --- |
| `No module named xspcomm` | 检查当前环境的安装或上述 PYTHONPATH |
| 加载路径是旧构建 | 修正环境，选中需要的 绑定 |
| 缺少 half-step API / XTriggerEngine | 更新或重建包含这些接口的 xcomm |

仓库完整回归会尝试发现同级 Picker 构建；普通脚本和仅运行 `examples/` 不使用
`tests/conftest.py` 中的发现逻辑，因此这里仍需让当前 Python 能直接导入 xspcomm。

## 相同的 Agent，在原生时钟上验证溢出

文件：`examples/getting_started/native_lab.py`

<!-- executable-example: examples/getting_started/native_lab.py -->
```python
"""Optional native-clock exercise, run explicitly after installing xspcomm."""

import pytest

from examples.getting_started.test_accumulator import AccumulatorModel, make_pipeline_agent
from examples.getting_started.toy_dut import TutorialDut
from toffee import Execution


@pytest.mark.asyncio
async def test_accumulator_with_native_clock():
    dut = TutorialDut(backend="native")
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(250)
            second = agent.submit(10)
            await agent.finish(timeout_cycles=10, observe_cycles=1)
            assert await first == 250
            assert await second == 4
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/native_lab.py
```

预期 `1 passed`。这里唯一与主线不同的环境选择是 `TutorialDut(backend="native")`；
它用原生 XClock 和 XData，但累加行为仍由 Python 替身实现。
所以它验证的是测试链路能在原生后端运行，**不是某个 RTL 累加器已通过验证**。
这个文件也是显式运行的选读练习，普通入门测试不要求安装原生绑定。

## 接入真实 RTL，要替换哪些地方

不同项目的 DUT 生成、端口和初始化方法不同，这里不放一个不存在的 `create_dut()`
作为可运行示例。实际迁移时按下面的对应关系实现项目环境：

| 教学代码 | 项目环境负责提供 |
| --- | --- |
| `TutorialDut()` | 由项目构建工具生成并创建的 DUT；必要的初始化序列 |
| `dut.clock` / `dut.backend` | 项目实际的 XClock，以及 `XCommClockBackend(clock)` |
| `Bundle(enable=..., operand=...)` | 实际端口绑定、位宽和空闲值 |
| 一拍接受的 SingleCycleDriver | 设计规定的接受条件和驱动时序；必要时编写具体 Driver |
| `dut.response is not None` | 真正的响应有效条件和采样时刻 |
| 整数快照 | 项目响应结构的不可变快照 |
| `AccumulatorModel.accept()` | 设计的功能规格、初始状态和状态更新规则 |
| 三周期响应预算 | 规格允许的响应范围 |
| `dut.close()` | 项目资源的实际清理方法 |

测试里的“输入 → 等待/结束检查 → 判定”结构可以保留；协议、reset、flush 规则须由项目明确。
不要把示例中拉高 enable 一拍的规则直接套给其他硬件。

## 按你的下一项任务继续

| 下一项工作 | 可以运行或阅读的例子 |
| --- | --- |
| 多周期输入、不同阶段可以重叠 | [流水输入场景](../guides/pipeline-examples.md)，含串行与重叠的时序对照 |
| 一个 Agent 只观察，不主动发输入 | [Monitor](../guides/monitors.md) 与 [Agent](../guides/agents-and-reference-models.md) 的被动实例 |
| 等信号条件，处理显式周期超时 | [Trigger](../guides/triggers.md) |
| 输出严格固定延迟，逐周期采样 | [Scoreboard 的关联与观察模式](../guides/scoreboard.md) |
| 每次运行保存 seed、功能覆盖率和 pytest 报告 | [验证流程配方](../guides/verification-flow.md) |
| 已有生成好的 DUT，想看项目环境组织 | [cache 环境说明](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/cache/README.md)（有额外项目依赖） |
| 把真实 DUT、模型、功能点和 RTL 行覆盖接起来 | [e203 环境说明](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/e203/README.md)，先定向、再随机、最后读统一报告 |

需要为本仓库做完整检查时，运行：

```bash
python3 -m pytest -q --require-xspcomm
python3 -m pip install -e '.[docs]'
mkdocs build --strict
```

`--require-xspcomm` 会在原生绑定 不可用时直接报错，避免把未运行的原生回归
当成通过。它是仓库 `tests/` 的检查选项，不需要加在前面的入门命令里。

[返回教程路线](index.md) · [查看全部功能指南](../reference/feature-map.md)。
