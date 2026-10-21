# 运行测试与汇总覆盖率

先确认测试能由 pytest 单独运行，再用 `xreactor run` 收集覆盖率和生成报告。
在源码仓库中安装运行依赖：

```bash
python3 -m pip install -e '.[runner]'
xreactor run tests/ --workers 8 --output output/regression-001
```

`python3 -m xreactor run` 与 `xreactor run` 等价。
pytest 负责运行测试和 fixture，pytest-xdist 把测试分给独立进程，
XReactor 负责收集、合并和生成覆盖率报告。测试里不需要自己创建收集器。

## 选择用例和进程数

```bash
# 只运行名称中含有 prediction 的测试。
xreactor run tests/ --workers 8 --output output/prediction-001 -k prediction

# 单进程诊断，仍然使用相同的收集和报告流程。
xreactor run tests/ --workers 1 --output output/serial-001
```

`-k`、`-m`、`-q` 和 `--tb=short` 等参数会传给 pytest。
`--collect-only` 只列出测试，不创建测量目录。
`--output` 指定的目录必须尚不存在，避免不同运行的结果混在一起。

## 功能覆盖率怎样收集

按平常的方式创建 covergroup，并在观察到事件时采样即可。
XReactor 会在测试清理完成后收集该测试创建的组，最后合并各进程的结果。
在模块或 session fixture 中创建的共享组，会在进程退出时收集一次。

合并会累加计数，并保留零命中的 bin。定义、实例名和采样规则必须兼容，
不能把含义不同的覆盖模型强行拼成一份报告。规则见[功能覆盖率](coverage.md)。

默认不逐例导出 JSON。各进程先写压缩后的中间结果，主进程合并后直接生成 HTML，
再删除已经成功合并的中间文件。需要最终功能覆盖率数据库时，添加：

```bash
xreactor run tests/ --workers 8 --output output/export-001 --export-coverage-json
```

如果旧测试中仍调用 `write_json()`，那些文件还会照常生成。
迁移到自动收集后，可以删掉仅用于跨用例汇总的手动导出，避免一份数据保存多次。

### 不把模型自测算成 DUT 覆盖率

覆盖模型本身的单元测试可能会人为填满 bin。给这种测试加上标记：

```python
import pytest


@pytest.mark.xreactor_functional_coverage(False)
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

项目插件可以实现 `pytest_xreactor_coverage(config, functional, native, pytest_exitstatus)`。
这个 hook 在主进程合并完成后、提交报告前调用一次，适合检查项目要求或输出小型摘要。
参数中的合并结果只供读取，不应修改。

hook 抛出异常会使收集失败，并保留可用的中间输入；不会把不满足要求的结果标成成功。
普通项目不需要实现它。

## 原生 DUT 覆盖率怎样收集

Picker/Verilator 适配器从 fixture 中查找提供 `SetCoverage`、`GetCovMetrics`、
`GetXClock` 和 `Finish` 的 DUT。fixture 可以直接返回 DUT，也可以返回带 `dut`
的 Agent，或用 `_instances` 保存实例的工厂。

fixture 仍负责初始化 DUT 和调用 `Finish()`。运行器不接管仿真时钟，也不关闭 backend。
如果工厂会在测试中提前关闭并释放 DUT，需要在创建时通过
`xreactor_register_dut` fixture 提供的回调登记它，避免清理时已经找不到对象。
登记不会推进或关闭 DUT。

每个进程先分批合并原生计数，主进程再按 DUT 构建身份合并。
同一构建最终只保留一份 `native/<build-id>/coverage.dat`，不会随进程数保存多份。
不同构建不会互相合并。零点、toggle 和其他原生计数也会保留，不只保留命中行。
每次合并都会核对输出计数是否等于输入之和。

覆盖率处理器优先使用 `build-provenance.json` 中的路径，否则查找 PATH 上的
`verilator_coverage`。也可显式指定：

```bash
xreactor run tests/ --output output/native-001 \
  --native-coverage-tool /path/to/verilator_coverage
```

只创建一个独立时钟对象，并不足以让运行器找到它背后的模拟器或导出文件。
DUT 应由 fixture 管理，或在创建时显式登记。

## 结果在哪里，失败时保留什么

| 文件 | 用途 |
| --- | --- |
| `coverage.html` | 查看汇总覆盖率和测试状态 |
| `manifest.json` | 查看运行是否完整、测试判定和产物身份 |
| `native/<build-id>/coverage.dat` | 每种 DUT 构建的完整原生计数 |
| 原生 LCOV 文件 | 查看行和分支覆盖率 |

添加 `--keep-coverage-inputs` 可保留各进程中间结果和原始导出，便于排查合并问题。
正常回归不需要打开它。

最终产物全部写好并校验后，manifest 才会标为 `committed`，随后回收本轮中间输入。
收集中断、输入损坏、测试收集不完整或进程崩溃时，会标为 `incomplete` 并保留可用输入。
如果只在删除中间文件时出错，已经提交的结果仍保留，错误记在 `cleanup_error`。

**测试失败与覆盖率收集失败是两件事。** 测试 FAIL 仍返回非零退出码，
但已经完整执行的失败回归也能生成诊断覆盖率。报告生成成功不代表 DUT 验证通过。

运行器只回收本轮自己创建的中间文件，不自动删除历史运行目录。
