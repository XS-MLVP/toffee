# Toffee

[![PyPI](https://img.shields.io/pypi/v/uc-toffee?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/uc-toffee/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?style=flat-square&logo=python&logoColor=white)](https://github.com/XS-MLVP/toffee/blob/main/docs/getting-started/installation.md)
[![CI](https://img.shields.io/github/actions/workflow/status/XS-MLVP/toffee/ci.yml?branch=main&style=flat-square&label=CI&logo=github)](https://github.com/XS-MLVP/toffee/actions/workflows/ci.yml?query=branch%3Amain)
[![License](https://img.shields.io/badge/license-MulanPSL--2.0-blue?style=flat-square)](https://github.com/XS-MLVP/toffee/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-Guide-0A7EA4?style=flat-square)](https://github.com/XS-MLVP/toffee/blob/main/docs/index.md)
[![GitHub Stars](https://img.shields.io/github/stars/XS-MLVP/toffee?style=social)](https://github.com/XS-MLVP/toffee/tree/main)

Toffee 是一个 Python 硬件验证框架。用 `async` / `await` 描述输入时序与条件等待，
用 pytest 运行测试，再通过参考模型、结果检查和覆盖率构建完整的验证环境。

- **时钟与事件**：等待时钟边沿、信号条件和跨周期事件；支持将条件编译到原生后端求值。
- **驱动与检查**：用 Driver、Monitor 和 Agent 组织可复用组件，通过 Scoreboard 检查顺序或乱序响应。
- **覆盖与回归**：定义功能覆盖、交叉覆盖和时序覆盖，在多进程回归后汇总功能与原生覆盖率。

## 快速开始

需要 **Python 3.11+**。PyPI 分发名为 `uc-toffee`，导入名为 `toffee`，
原生绑定由依赖 `uc-xcomm` 提供。正式包发布后可用 `pip install "uc-toffee[runner]"` 安装。
下面在仓库根目录创建环境并从源码安装，以便运行仓库自带的例子：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
```

下面用仓库自带的 8 位累加器模型演示一个测试，无需安装 RTL 仿真器。
测试送入数字 3，等待一个时钟周期，再检查累加结果与空闲时的行为：

```python
import pytest

from toffee import ClockCycles, Execution
from examples.getting_started.toy_dut import TutorialDut


@pytest.mark.asyncio
async def test_one_input_and_idle():
    dut = TutorialDut()
    try:
        async with Execution(dut.backend):
            assert dut.total.U() == 0
            dut.operand.Set(3)
            dut.enable.Set(1)
            await ClockCycles(dut.clock, 1)
            dut.enable.Set(0)
            assert dut.total.U() == 3

            await ClockCycles(dut.clock, 2)
            assert dut.total.U() == 3
    finally:
        dut.close()
```

这个用例已包含在[示例文件](https://github.com/XS-MLVP/toffee/blob/main/examples/getting_started/test_accumulator.py)中，可以直接运行：

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_one_input_and_idle
```

预期得到 `1 passed`。继续阅读[累加器教程](https://github.com/XS-MLVP/toffee/blob/main/docs/getting-started/index.md)，
逐步加入边界测试、Driver、Monitor、参考模型与覆盖率。

## 用于项目验证

接入真实 RTL 时，使用 Picker 生成的 DUT Python 包，通过 `XCommClockBackend`
连接 xspcomm 的时钟与信号。具体准备和依赖要求见[真实 DUT 接入指南](https://github.com/XS-MLVP/toffee/blob/main/docs/getting-started/native-and-next.md)。

测试增多后，用 `toffee run` 并行执行并汇总覆盖率。例如，运行整个入门示例目录：

```bash
toffee run examples/getting_started --workers 4
```

终端会显示本次运行的产物目录；有覆盖数据时会生成 `coverage.html`。
将示例路径换成项目测试目录即可运行自己的回归，更多选项见[回归运行指南](https://github.com/XS-MLVP/toffee/blob/main/docs/guides/runner.md)。

## 文档

- [入门教程](https://github.com/XS-MLVP/toffee/blob/main/docs/getting-started/index.md)：从一条断言到完整的验证环境。
- [操作指南](https://github.com/XS-MLVP/toffee/blob/main/docs/guides/index.md)：接入 DUT、组织组件、采集覆盖率和运行回归。
- [接口参考](https://github.com/XS-MLVP/toffee/blob/main/docs/reference/index.md)：API、命令行参数和行为限制。
- [原理说明](https://github.com/XS-MLVP/toffee/blob/main/docs/explanation/index.md)：仿真调度、资源归属与覆盖率含义。
- [可运行示例](https://github.com/XS-MLVP/toffee/blob/main/docs/examples.md) · [参与开发](https://github.com/XS-MLVP/toffee/blob/main/CONTRIBUTING.md)

## Toffee Legacy

旧版 Toffee 保留在 [`master` 分支](https://github.com/XS-MLVP/toffee/tree/master)。
当前版本是一次 **breaking change**，与 Toffee Legacy 的 API 不兼容。
