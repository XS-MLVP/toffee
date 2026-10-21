# XReactor

XReactor 是基于 `asyncio` 的 Python 硬件验证框架。测试协程可以直接等待时钟边沿、
信号条件、时序序列和外部异步事件。可编译的条件与状态机由 xcomm trigger engine
执行。

本分支是 coverage v2 草稿，适配 picker/xcomm 的 `xreactor-coverage-v2` 分支。
配套版本为 xspcomm `0.3.0.dev0`、原生 ABI 3、coverage 描述协议 4；精确提交和
构建方法见 [coverage v2 适配说明](docs/guides/coverage-v2-trigger-bins.md#pickerxcomm-分支适配)。

第一次写验证测试，从[零基础教程](docs/getting-started/index.md)开始。用一个小累加器，
先学会输入、等待时钟和检查结果，再逐步加入 Driver、Monitor、Agent、参考模型、
流水提交和覆盖率。主线集中在一个测试文件，配有逐步解释、运行结果和改错练习，
无需 RTL 或仿真器。

## 安装

```bash
python3 -m pip install -e .
```

统一运行入口支持自动收集覆盖率、多 worker 合并和中间产物回收：

```bash
python3 -m pip install -e '.[runner]'
xreactor run tests/ --workers 8
```

默认不生成逐例覆盖率 JSON；完整用法和原生 DUT 适配见
[统一 CLI 与多 worker](docs/guides/runner.md)。

开发与测试依赖：

```bash
python3 -m pip install -e '.[test]'
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_addition
python3 -m pytest -q
```

完整的原生后端回归必须提供带 `XTriggerEngine` 的 xspcomm，并启用强制检查：

```bash
XCOMM_PYTHON=/path/to/xcomm/build/python \
  python3 -m pytest -q --require-xspcomm
```

若 xreactor 与 picker 是同级目录，测试会自动发现
`../picker/build/dependence/xcomm/python`。

真实 DUT 需要提供 xcomm/xspcomm 的 `XClock`、`XData` 和 `XTriggerEngine` Python
binding。纯 Python 示例使用 `MemoryBackend`，无需仿真器：

```bash
PYTHONPATH=src python3 examples/triggers/basic_execution.py
```

## 文档

- [安装](docs/getting-started/installation.md)
- [Toffee v2 迁移说明](docs/guides/toffee-v2-migration.md)
- [从零开始的学习路线](docs/getting-started/index.md) · [第一条断言](docs/getting-started/first-test.md)
- [Trigger](docs/guides/triggers.md)
- [编译条件、Sequence 和 FSM](docs/guides/compiled-triggers.md)
- [信号与 Bundle](docs/guides/data.md)
- [Driver](docs/guides/drivers.md) · [Monitor](docs/guides/monitors.md)
- [Agent 与参考模型](docs/guides/agents-and-reference-models.md)
- [Scoreboard 与流水线事务](docs/guides/scoreboard.md)
- [流水输入与阶段重叠示例](docs/guides/pipeline-examples.md)
- [asyncio 与 pytest](docs/guides/asyncio-and-pytest.md)
- [Functional coverage](docs/guides/coverage.md)
- [功能点与跨用例覆盖](docs/guides/functional-points.md)
- [功能与回归索引](docs/reference/feature-map.md) · [API 索引](docs/reference/api.md)

完整目录见[用户手册](docs/index.md)。维护者设计记录位于 [design](design/README.md)。
提交检查、PR 版本标签与发布流程见[版本、CI 与发布](docs/guides/versioning-and-releases.md)。

本地预览文档：

```bash
python3 -m pip install -e '.[docs]'
mkdocs serve
```
