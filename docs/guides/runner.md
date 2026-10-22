# 运行测试与汇总覆盖率

本页完成一次项目回归：选择用例、收集覆盖率、查看结果，并定位收集失败。
前提是测试已能由 pytest 单独运行。先在源码仓库中安装运行依赖：

```bash
python3 -m pip install -e '.[runner]'
toffee run tests/ --workers 8 --output output/regression-001
```

把 `tests/` 换成项目的测试目录，`--output` 使用新目录。
结束时终端会显示产物目录；有覆盖数据时打开其中的 `coverage.html`，
同时检查 pytest 判定和命令退出码。全部选项与文件定义见[Runner 参考](../reference/runner.md)。

## 选择用例和进程数

```bash
# 只运行名称中含有 prediction 的测试。
toffee run tests/ --workers 8 --output output/prediction-001 -k prediction

# 单进程诊断，仍然使用相同的收集和报告流程。
toffee run tests/ --workers 1 --output output/serial-001
```

`-k`、`-m`、`-q` 和 `--tb=short` 等参数会传给 pytest。
`--collect-only` 只列出测试，不创建测量目录。
`--output` 指定的目录必须尚不存在，避免不同运行的结果混在一起。

## 功能覆盖率怎样收集

按[功能覆盖率指南](coverage.md)创建 covergroup，在观察到事件时采样。
测试里无需创建收集器，Runner 会自动捕获并合并数据。
需要保存最终功能覆盖率数据库时，添加：

```bash
toffee run tests/ --workers 8 --output output/export-001 --export-coverage-json
```

如果旧测试中仍调用 `write_json()`，那些文件还会照常生成。
迁移到自动收集后，可以删掉仅用于跨用例汇总的手动导出，避免一份数据保存多次。

### 不把模型自测算成 DUT 覆盖率

覆盖模型本身的单元测试可能会人为填满 bin。给这种测试加上标记：

```python
import pytest


@pytest.mark.toffee_functional_coverage(False)
def test_coverage_definition():
    # 在这里测试分桶或合并规则，不计入正式功能覆盖率。
    ...
```

标记对该测试的 fixture 初始化、测试执行和清理都生效，但不会关闭原生 DUT 覆盖率收集。
不要把这类人为样本与真实 DUT 的样本放进同一个 session 级共享组。

真实场景里如果另建了一个只用于局部断言的辅助组，可以使用
`definition.instantiate("helper", capture=False)`。它仍可采样、检查和手动导出，
但不进入自动汇总。

### 在合并后增加项目检查

项目需要检查合并后的覆盖目标时，在项目插件中实现 `pytest_toffee_coverage`。
在 hook 内读取合并结果并检查目标；失败时抛出异常，Runner 会保留可用输入并报告失败。
完整签名和调用时机见[Runner 参考](../reference/runner.md)。普通回归无需实现这个 hook。

## 原生 DUT 覆盖率怎样收集

让 fixture 管理 Picker/Verilator DUT 的初始化和 `Finish()`，并返回 DUT、带 `dut`
的 Agent，或用 `_instances` 保存实例的工厂。确认生成包提供
[自动发现所需接口](../reference/runner.md)。

如果工厂会在测试中提前关闭并释放 DUT，在创建时通过 `toffee_register_dut` fixture
提供的回调登记对象，避免清理时已经找不到它。独立时钟对象不足以定位 DUT 的导出文件。

运行时会按 DUT 构建身份合并原生计数。需要使用特定覆盖率处理器时显式指定：

```bash
toffee run tests/ --output output/native-001 \
  --native-coverage-tool /path/to/verilator_coverage
```

Runner 不接管仿真时钟，也不关闭 Backend；沿用项目 fixture 的资源清理流程。

## 结果在哪里，失败时保留什么

先查看 pytest 错误输出，再读取本次目录的 `manifest.json`。

| 现象 | 下一步 |
| --- | --- |
| 提示输出目录已存在 | 为新一轮选择另一个目录，不覆盖上一轮证据 |
| manifest 为 `committed`，但有测试 FAIL | 根据断言或 Scoreboard 诊断修复测试；可用报告辅助定位场景 |
| manifest 为 `incomplete` | 阅读 `errors` 和 worker 日志，检查崩溃、收集不完整或覆盖模型不兼容；保留现有输入 |
| 有 `cleanup_error` | 检查目录权限和文件占用；已经提交的最终产物仍有效 |
| 测试通过但没有 `coverage.html` | 检查是否实际创建并采样了覆盖组，或是否成功发现原生 DUT |

需要保留成功运行的各进程中间结果以便比较时，在新一轮加上 `--keep-coverage-inputs`。
汇总失败本身会保留可用输入，不需要先重跑才能获取这些文件。
文件与状态的完整定义见[Runner 参考](../reference/runner.md)。

报告是否完整和 DUT 是否通过检查需要分别判断，原因见
[检查、覆盖率与回归结果](../explanation/coverage-results.md)。
