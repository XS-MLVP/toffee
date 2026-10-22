# 真实 e203 IFU-to-ICB 验证示例

本例通过 Picker memory-direct DUT 和 native XClock 验证 `e203_ifu_ift2icb`。
先读[累加器教程](../../getting_started/README.md)；这里进一步展示一个输入可能产生
零、一或两条 ICB 命令时，如何用项目环境、独立模型、Scoreboard 和覆盖率完成检查。

## 构建与运行

需要 Picker、Verilator、C++ 编译器、当前 xspcomm，以及提供 `genhtml` 的 lcov。
在 Toffee 仓库根目录执行，替换本机工具路径：

```bash
python3 -m pip install -e '.[test]'
export PICKER_ROOT=/path/to/picker
export PICKER_BIN="$PICKER_ROOT/build/bin/picker"
BUILD_THREADS=8 examples/integration/e203/build_toffee.sh
examples/integration/e203/run_pytest.sh
```

生成目录默认 `output/toffee_e203`，输出默认 `output/e203-verification`。
旧生成包若缺少 `XTriggerEngine.ClearExecutionState`，更新 Picker 使用的 xspcomm 后
重新构建 DUT；只安装 Python 框架不能更新生成包内的 native binding。

先关闭随机流量：主 campaign 跑 34 笔定向事务，其余数据模式、故障和清理检查一并运行。

```bash
E203_RANDOM_COUNT=0 E203_ARTIFACT_DIR=output/e203-directed \
  examples/integration/e203/run_pytest.sh
```

再加随机流量，换 seed 和独立输出目录即可复现或比较：

```bash
E203_SEED=0x1234 E203_RANDOM_COUNT=500 E203_ARTIFACT_DIR=output/e203-seed-0x1234 \
  examples/integration/e203/run_pytest.sh
```

预期 `31 passed`；默认主 campaign 检查 534 笔 fetch
（34 笔定向 + 500 笔随机）。这些是 IFU-to-ICB 模块的测试，不是整颗 e203 CPU 的验证。

构建脚本启用内部观察。`E203InternalObserver` 用 `DriveStable` 和紧邻上升沿的快照
检查 49 项 lane、FSM、上下文、leftover、路由与缓冲要求；信号缺失或宽度错误直接失败。
`test_e203_internal_scenarios` 根据真实缺口补充同拍交接、缓冲竞争、不同 ready、长阻塞、
错误清除、非选中响应和在途复位。每个必需场景都有实际比较，要求全部内部 bins 命中。
四个检查器反例分别验证上下文、leftover、状态和缓冲比较能拒绝错误快照。
另外三项在途复位分别中断首响应等待、第二命令受阻和已缓冲输出，保存复位前后状态，
并比较恢复后的 split fetch。

每个 case 的 `internal-functional-coverage.json` 保存 bin 命中及来源；
`internal-checks.json` 分开保存比较次数、错误、未覆盖场景和 `closed` 状态。
检查器反例写入 `checker-counterexample-coverage.json`，不贡献 DUT 覆盖。

## 场景与代码如何对应

| 场景 | 检查内容 |
| --- | --- |
| ITCM / BIU、lane begin/middle/cross | 路由、命令地址、little-endian 指令拼接 |
| holding data、顺序 +2/+4、nohold | 每次 fetch 的 0/1/2 命令优化及跨 lane 行为 |
| 连续切换 holding/nohold、错误后复用、跳转 | 强制重新取数、拆分错误和后续请求之间的状态隔离 |
| ITCM 区域边界与 32 位地址回绕 | 两条命令可路由到不同端口；地址计算按位宽回绕 |
| 首命令 / 第二命令 / 输出背压 | 被阻塞时的请求和响应字段稳定，WAIT2ND 后能继续完成 |
| 首次、第二次、同时报错 | 两次 ICB 错误的逻辑 OR |
| 两次响应独立延迟 | ITCM/BIU 分别检查首响应较慢、第二响应较慢 |
| 全零、全一、交替位、walking bit 数据 | 每种模式重跑全部定向场景，检查不同数据下的拼接 |
| 漏响应与恢复 | 5 周期预算确定失败；显式 reset 后下一次 fetch 正常 |
| 截止周期 | ITCM/BIU 普通和跨 lane 路径，恰好到预算成功、少一周期超时 |
| 结束观察 | 最后响应之后继续观察 3 周期，拒绝额外命令或响应 |
| 检查器故障回归 | 错指令/错误位/命令地址、漏命令/响应、额外输出，以及背压时改值或撤回 valid、接受前响应 |
| 外部取消 | 接受前、等待 ICB 响应、输出受阻三个阶段，检查任务、watcher、backend ownership 和驱动 valid 清理 |

`e203_toffee_env.py` 将项目时序集中在 `E203Harness`；`IfuReferenceModel.predict()`
只计算期望，`accept()` 在输入真正接受后更新模型状态。输入用 Bundle 驱动，响应同步
采样为快照，Scoreboard 同时比较命令序列、指令及错误，并携带接受/响应时刻。
该 harness 的 `finish()` 是项目的有限尾部检查，不是异步 Scoreboard 的通用 `finish()`。

`test_e203_toffee.py` 集中组织所有测试：1 项主 campaign、1 项超时恢复、4 项数据模式、
4 项周期边界、10 项故障回归和 3 项取消检查；没有按 feature 拆出新文件。
模型、DUT command 记录和不可变 `FetchTransaction` 供比较和覆盖率共同使用，覆盖采样不回读引脚。

故障回归在真实 DUT 运行时修改送给检查器的快照或命令记录，不修改 RTL。
测试要求检查器抛出指定异常，正确拒绝错误才算通过。比如响应在背压期间撤掉一拍 valid、
随后恢复，检查器也必须拒绝，不能只比较 valid 为高时的数据。

只运行关心的场景，可用 pytest 的 `-k`：

```bash
examples/integration/e203/run_pytest.sh -k 'data_patterns or exact_response_budget'
examples/integration/e203/run_pytest.sh -k 'checker_rejects_faults or external_cancellation'
```

这两条分别运行 8 项、13 项检查；只有选择主 campaign 时才生成 RTL 源码覆盖站点。
主 campaign 默认使用地址生成的数据，也可通过 `E203_DATA_PATTERN=walking` 切换模式；
可选值为 `address`、`zero`、`ones`、`alternating`、`walking`。

## 功能点与覆盖场景

[verification_plan.json](verification_plan.json) 的 `requirements` 保留设计与验证器要求，
说明前置条件、激励、预期行为、观察点及证据位置。它是设计评审材料，不是 pytest 输入。
用例共用 `e203_coverage_definition()` 定义的 bins/cross，由环境对实际事务采样。
当前采样发生在事务完成并比较成功后，因此它度量“检查成功的事务场景”，不涵盖失败
之前的所有内部活动；故障检测与取消测试的通过数量也不计入 DUT 功能覆盖率。

主 campaign 额外检查定向场景快照，确认两种路由、错误组合、holding 切换和独立延迟
确实出现。这是对该定向序列有效性的检查，普通 case 无需独立命中整个覆盖模型。

内部清单的 49 项 lane 判定、FSM、上下文、leftover、mux、命令准入、响应缓冲及
复位要求已接入边沿属性检查。专门的内部场景 case 包含重叠输入和事务中途 reset；
四个伪造快照反例独立检查验证器能报错。具体采样条件见
[属性观察器](e203_internal_coverage.py)，设计义务与必需场景见[验证计划](verification_plan.json)。

不同 seed 的同一覆盖模型可以合并，保持稳定的 `e203_ifu_ift2icb` 实例名。选择同一 RTL/配置的
运行目录，显式传入各份产物，不要重复输入同一文件：

```bash
toffee-coverage-report \
  --functional output/e203-directed/functional-coverage.json \
  --functional output/e203-seed-0x1234/functional-coverage.json \
  --output output/e203-combined.html
```

单独运行一项故障检查仍使用普通 pytest 选择，无需加载计划：

```bash
examples/integration/e203/run_pytest.sh -k 'checker_rejects_faults and withdrawn'
```

## 产物与故障定位

运行脚本结束后打开 `coverage-report/index.html`。统一站点展示
15 个 coverpoint / 4 个 cross 的实际命中及 RTL 逐行覆盖；pytest 单独报告执行结果。
100% 功能覆盖不等于 100% RTL 行覆盖。

`line-coverage.info/json` 保留 Picker 生成封装与全部计数类型。历史 67%（201/300）
包含 98 行未执行的 DPI getter/setter；memory-direct 不调用它们。2026-10-02 复测
31 个真实 DUT 测试，单独统计 DUT 及 helper RTL 的执行计数得到 21/21 行、23/23
覆盖点；全部计数类型得到 173/173 行。主 campaign 单独为 172/173，差异是常量
`state_wait2nd_nxt` 的初始化 toggle，不是新增事务路径。
这些数字只说明本构建的已插桩范围，不表示所有组合逻辑行为已验证。
可用 [rtl_line_coverage.py](../rtl_line_coverage.py) 分别报告执行计数、全部计数和封装；
保持原始数据库，不能把封装范围调整解释成新增 DUT 命中。

目录还包含 `functional-coverage.json`、`transactions.json`、`commands.json`、
`performance.json`、原始 `verilator-coverage.dat`、`line-coverage.info/json` 和单页
`coverage-report.html`。`performance.json` 带 seed、随机数量、数据模式、campaign 状态及吞吐量。
失败时先查看其中的 `error` 和 `active_request`：后者记录请求 PC、接受时刻、最后采样时刻
及命令数量。再用 `commands.json` 检查未完成 fetch 已发出的命令，`transactions.json`
保留已经检查通过的事务。单项故障和数据模式测试的产物在各自的 pytest 临时目录内，
可用 `--basetemp output/e203-cases` 指定本次测试的临时根目录。

campaign 失败仍导出已有数据，保存原异常；报告生成失败由 pytest 明确报错。
正常结束、失败和外部取消都会撤销测试环境驱动的请求/ICB 响应 valid 并释放 backend，
不把未完成事务记为成功；需要继续使用 DUT 时由调用方显式 reset。
pytest 的最终结果以原生报告和退出码为准；需要机器可读结果时可传 `--junitxml=output/e203-results.xml`。

可选环境变量：`E203_DUT_DIR`、`E203_WALL_TIMEOUT`（默认 60 秒）、
`E203_MIN_TRANSACTIONS_PER_SECOND`（默认 0，不设性能门槛）、`E203_TRACE=1`。

如需测量 coverage 采样开销，使用同一 DUT/seed 的交替 A/B benchmark：

```bash
python3 examples/integration/e203/benchmark_coverage.py \
  --dut-dir output/toffee_e203 --random-count 500 --repeats 9 --json
```

运行报告与覆盖数据留在本地产物目录，不纳入 Git。验证范围由本例的配置、
[验证计划](verification_plan.json)及实际检查代码定义。
