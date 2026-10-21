# XReactor

XReactor 是用 Python 编写硬件测试的框架。你可以发送输入、等待时钟、检查结果，
再把重复的工作放进 Driver、Monitor 和参考模型。测试由 pytest 运行，
功能覆盖率和原生覆盖率可以在多进程回归后自动汇总。

第一次使用，建议从[入门教程](docs/getting-started/index.md)开始。
教程用一个 8 位累加器，先写出第一个通过和失败的测试，再逐步加入时钟、
流水线、自动检查和覆盖率。前半部分只需 Python，不需要 RTL 或仿真器。

## 安装并运行第一个测试

需要 Python 3.11 或更新版本。在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_addition
```

看到 `1 passed` 后，继续[输入与时钟](docs/getting-started/clock-and-input.md)。
安装遇到问题时，查看[环境准备](docs/getting-started/installation.md)。

## 运行项目回归

```bash
xreactor run tests/ --workers 8 --output output/regression-001
```

pytest-xdist 把测试分给独立进程，XReactor 汇总覆盖率并生成报告。
输出目录必须是新目录，避免两轮结果混在一起。默认不保留逐例覆盖率 JSON。
参数和产物说明见[运行测试与汇总覆盖率](docs/guides/runner.md)。

## 接入真实 DUT

纯 Python 示例使用 `MemoryBackend`。真实 DUT 可以使用 Picker 生成的 Python 包，
通过 xspcomm 提供的 `XClock`、`XData` 和 `XTriggerEngine` 接入。
驱动与检查代码仍由项目定义，不需要改成另一套测试写法。

先阅读[原生后端与项目 DUT](docs/getting-started/native-and-next.md)。
开发时所需的 xcomm 提交、包版本、ABI 和协议版本统一记录在
[依赖清单](.github/native-dependencies.json)，不要混用不同版本的生成包和运行库。

## 按需要查文档

- [从零开始](docs/getting-started/index.md)：安装、断言、输入、时钟和逐步练习。
- [Driver](docs/guides/drivers.md)、[Monitor](docs/guides/monitors.md)、
  [Agent 与参考模型](docs/guides/agents-and-reference-models.md)：组织可复用的验证环境。
- [Scoreboard](docs/guides/scoreboard.md)：比较结果，关联流水线中的输入与输出。
- [功能覆盖率](docs/guides/coverage.md)：定义需要测到的场景，查看和合并实际命中。
- [用类定义覆盖模型](docs/guides/coverage-classes.md)、
  [时序覆盖](docs/guides/coverage-patterns.md)：组织较大的模型，统计跨周期事件。
- [运行测试与汇总覆盖率](docs/guides/runner.md)：多进程回归、报告和失败排查。
- [可运行示例](docs/examples.md)、[API 索引](docs/reference/api.md)：按功能查代码。

完整目录见[用户手册](docs/index.md)。参与开发请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 本地预览文档

```bash
python3 -m pip install -e '.[docs]'
mkdocs serve
```
