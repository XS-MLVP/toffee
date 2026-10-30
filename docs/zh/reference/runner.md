# Runner 参考

`toffee run` 使用 pytest 执行测试，并收集、合并覆盖率。
安装和操作步骤见[运行测试与汇总覆盖率](../guides/runner.md)，
结果含义见[检查、覆盖率与回归结果](../explanation/coverage-results.md)。

## 命令与选项

```text
toffee run [Toffee 选项] [pytest 参数]
python3 -m toffee run [Toffee 选项] [pytest 参数]
```

两种入口等价。运行需安装 pytest；多进程运行还需 pytest-xdist。

| 选项 | 默认值 | 行为 |
| --- | --- | --- |
| `--workers N`、`-j N` | `1` | 正整数；大于 1 时使用独立 worker 进程 |
| `--output PATH` | `output/toffee-<UTC 时间>-<随机标识>` | 结果目录，必须尚不存在 |
| `--keep-coverage-inputs` | 关闭 | 成功汇总后保留各进程中间输入和原始导出 |
| `--export-coverage-json` | 关闭 | 有功能覆盖数据时，额外保存最终合并的 JSON |
| `--native-coverage-tool PATH` | 未指定 | 显式选择 Verilator 覆盖率处理器；否则优先使用构建 provenance 路径，再查 PATH |

测试路径、`-k`、`-m`、`-q`、`--tb=short` 等参数传给 pytest。
`--collect-only` 只列出测试，不创建测量目录；已存在的 `--output` 路径仍会被拒绝。
`--` 可显式分隔后续 pytest 参数。

并发数通过 `--workers` 设置，不能透传 `-n`、`--numprocesses`、`--dist`、`--tx` 或
`--max-worker-restart`。Runner 使用 load 调度，禁止 worker 自动重启，以便识别不完整的回归。

## 最终产物

路径相对于本次 `--output`。产物清单及身份记录在 `manifest.json`。

| 路径 | 生成条件与内容 |
| --- | --- |
| `manifest.json` | 记录运行状态、测试判定、产物与完整性；失败信息视失败阶段保留 |
| `coverage.html` | 存在功能或原生覆盖数据时生成的汇总报告；没有覆盖数据时不会生成 |
| `functional-coverage.json` | 有功能覆盖数据且启用 `--export-coverage-json` 时生成 |
| `native/<build-id>/coverage.dat` | 按 DUT 构建身份合并的完整原生计数 |
| `native/<build-id>/coverage.info` | 原生工具支持 LCOV 导出时生成的行、分支覆盖数据 |
| `_workers/` | 中间输入；成功提交后默认回收，启用保留选项或汇总失败时保留可用文件 |

默认不逐例导出 JSON。测试里显式调用的 `write_json()` 不受这个默认值影响。
不同 DUT 构建的原生计数不互相合并；同一构建最终保留一份 `coverage.dat`。
合并保留零点、toggle 及其他原生计数，并核对输出计数等于输入之和。

## 状态与退出行为

| 情况 | 结果 |
| --- | --- |
| 测试执行完整，产物全部写好并校验 | manifest 为 `committed`；测试判定仍由 `pytest_exitstatus` 和测试计数表示 |
| 测试断言失败，但执行和收集完整 | 仍可提交诊断报告，命令返回非零 |
| 收集中断、输入损坏、收集不完整或 worker 崩溃 | manifest 为 `incomplete`，保留可用输入；最终产物可能不齐全 |
| 产物已提交，仅中间文件清理失败 | 保持 `committed`，记录 `cleanup_error`，命令返回非零 |

Runner 通常返回 pytest 的退出码；覆盖收集或清理失败会把原本成功的运行改成非零退出。
CLI 参数错误返回 2，CLI 捕获到键盘中断时返回 130。
初始化阶段失败不保证已经创建 manifest；以命令退出和错误输出为准。
Runner 只回收本轮中间文件，不自动删除历史运行目录。

## 项目扩展接口

| 入口 | 契约 |
| --- | --- |
| `@pytest.mark.toffee_functional_coverage(False)` | 排除该测试及其 fixture 初始化、清理中的功能覆盖捕获；不关闭原生 DUT 覆盖收集 |
| `definition.instantiate("helper", capture=False)` | 该辅助组不进入自动汇总；仍可采样、断言和手动导出 |
| `toffee_register_dut` fixture | 提供显式登记 DUT 的回调，用于可能在测试中提前关闭和释放的对象；登记不推进或关闭 DUT |
| `pytest_toffee_coverage(config, functional, native, pytest_exitstatus)` | 主进程合并后、提交前调用一次；参数只供读取；抛异常使收集失败并保留可用输入 |

自动发现的 DUT 须提供 `SetCoverage`、`GetCovMetrics`、`GetXClock` 和 `Finish`。
fixture 可直接返回 DUT、返回带 `dut` 的 Agent，或返回用 `_instances` 保存实例的工厂。
fixture 仍负责初始化和 `Finish()`；Runner 不接管仿真时钟，也不关闭 Backend。
独立的时钟对象不足以定位背后的仿真器与导出文件。
