# 真实 Cache 验证示例

这个目录验证 Picker 的 `example/CacheSignalCFG/Cache.v`，使用生成的 memory-direct
DUT 和 native XClock。先完成[累加器教程](../../getting_started/README.md)；这里的
Cache 命令、接受条件和响应时序均属于项目协议，不能作为通用 Driver 的默认规则。

## 构建一次

需要 Picker、Verilator、C++ 编译器及当前 xspcomm。替换下面的 Picker 路径，在
Toffee 仓库根目录执行：

```bash
python3 -m pip install -e '.[test]'
export PICKER_ROOT=/path/to/picker
export CACHE_DUT_PARENT="$PWD/output/cache-dut"
"$PICKER_ROOT/build/bin/picker" export \
  --fs "$PICKER_ROOT/example/CacheSignalCFG/Cache.v" \
  --lang python --sname Cache --tname CacheSignalCFG \
  --tdir "$CACHE_DUT_PARENT/CacheSignalCFG" --sdir "$PICKER_ROOT/template" \
  --autobuild true --rw mem_direct --vpi --coverage -V '--no-timing --assert' -j 8
export PYTHONPATH="$CACHE_DUT_PARENT${PYTHONPATH:+:$PYTHONPATH}"
```

`--vpi` 提供失败诊断所需的内部状态；生成包必须含 `signal_tree`。若旧包缺少
`XTriggerEngine.ClearExecutionState`，需更新 Picker 使用的 xspcomm 后重新生成 DUT，
仅修改外部 PYTHONPATH 可能仍然加载生成包内的旧 binding。

## 先定向，再随机

不加随机输入也能跑完整定向场景：

```bash
python3 examples/integration/cache/cache_functional_toffee.py \
  --random-ops 0 --artifacts output/cache-directed
```

预期完成 129 笔 CPU 事务，其中 111 笔读数据经 Scoreboard 检查；另外完成 18 次
coherence probe、146 个响应 beat。然后增加随机压力：

```bash
python3 examples/integration/cache/cache_functional_toffee.py \
  --seed 0x1234 --random-ops 300 --artifacts output/cache-seed-0x1234
```

| 场景 | 实际检查 |
| --- | --- |
| 8 种 refill 起始 word | 首词地址、wrap 顺序、整行读回，随后全部 hit |
| write hit / write allocate | 合并后的数据，替换前 backing memory 保持旧值 |
| byte mask | 零掩码、8 个独立字节、部分掩码和全掩码 |
| clean / dirty replacement | 干净行不写回；脏 victim 的 8 个 word 全部正确 |
| 已有上下游背压 | 请求字段及响应数据、cmd、user 保持稳定 |
| MMIO | 读写数据、mask、内存旁路、下游请求数量 |
| 随机冲突 | 三个 set、每 set 十个 tag，独立逻辑内存检查读及写后读 |
| Monitor | 不可变请求快照与 responder 的事务记录逐笔相符 |
| coherence miss | 只返回 miss 头，无 refill / MMIO 流量 |
| clean / dirty probe hit | 命中头 + 8 beat，覆盖全部起始 word 的回绕顺序及脏字节 |
| coherence 背压与后续访问 | 头部、首/中/末数据拍背压；probe 后仍可命中读回，backing 保持旧值 |

`CacheDriver` 派生自 `SyncDriver`，`_drive_one()` 保留已有项目握手。
`read()` / `write()` 是本项目等待完整响应的便捷方法；不要将它们等同于通用 Driver
只等待输入接受的 `send()`。后台 memory/MMIO responder 留在环境内部。

## 用 Agent 封装 coherence 接口

`CoherenceAgent` 是项目实例，内部封装 `ProbeDriver(SyncDriver)` 和具名
`ReadyValidMonitor`。`main()` 把它注册给 `Execution(backend, agents=[coherence])`，
由 Execution 统一启动、关闭。测试使用 `await coherence.probe(...)`，不直接操作
asyncio TaskGroup 或锁。

一次 probe 有多拍响应，所以由项目方法收集具名 Monitor 的快照，再交给 Scoreboard
比较完整序列。预期值来自调用方传入的独立 line 模型：`line=None` 表示应 miss；
命中时传入按 line base 排列的 8 个 word，方法依据请求地址计算回绕顺序。
`stall_beat=0` 选择响应头，`1`～`8` 选择数据拍，默认停两周期。

默认输入接受预算 100 周期，接受后的整组响应预算 100 周期，每笔完成后继续观察
3 周期，拒绝额外响应；旧于输入接受时刻的响应也会失败。一次只允许一个 probe。
当前 DUT 的 probe 不使缓存行失效，不能将这条规则扩展成通用框架假设。

**末拍标志按这个 RTL 检查：** `cmd=6` 由实际接受条件参与生成。ready 保持为低时
检查 valid 和有效载荷稳定；接受时检查 header/数据/最后一拍的完整命令序列。
这里不承诺 ready 翻转前后 cmd 不变，也没有修改通用 Driver/Monitor 的规则。

## 看产物与失败

`--artifacts` 指定本次输出目录；比较多个 seed 时为每次运行分配不同目录。

- `summary.json`：CPU 事务、checked reads、refill、writeback、probe/beat 数和已观察问题。
- `functional-coverage.json` / `coverage-report.html`：CPU 覆盖组及 coherence state/start_word/stalled、state×stalled。
- `coherence-transactions.json`：每次 probe 的地址、接受 tick、逐拍 cmd/data/tick、状态；失败时保留已收到的拍。
- `run.json`：seed、随机输入数量、成功或失败状态和错误。
- `verilator-coverage.dat`：原始 RTL 覆盖数据。

检查失败时仍保存运行状态和已有功能覆盖率，后台任务、Monitor、Driver、backend 均清理。
覆盖率 100% 只表示上述已定义 bin 全部命中。并发及内部机制由下面的增量测试检查；
其他配置的合法 flush、未定义非法请求和所有 RTL 分支不在此覆盖结论内。

## 检查验证器确实能报错

已构建 DUT 并设置上面的 PYTHONPATH 后，显式运行一个项目回归文件：

```bash
python3 -m pytest -q examples/integration/cache/test_cache_coherence.py
```

预期 `8 passed`。除完整定向 campaign 外，它在 Monitor
交付边界注入错误响应头、错误数据、错误末拍、漏拍、额外拍和旧响应，并验证外部取消。
故障场景只有实际捕获预期失败才算通过；同时检查任务、watcher、信号 ownership 和
失败产物。测试不修改 RTL。该文件不加入普通框架/入门教程的默认测试收集。

### 功能点与覆盖场景

[verification_plan.json](verification_plan.json) 的 `requirements` 保留激励、观察点、
行为要求及范围，用于评审和设计 case，不作为 pytest 输入或测试完成率分母。
CPU 与 coherence 的统一覆盖模型由 `verify_cache()` 创建；实际完成并检查成功的访问
和 probe 才采样。各 case/seed 可以贡献同一模型，合并时累加实际 bin 命中。
测试数量、故障注入测试的通过结果不计为 DUT 功能覆盖。

现有 campaign 检查 CPU 数据、替换、MMIO 与 coherence，另核对 18 笔 probe 的拍数、
全部 start word 和头/首/中/末四个背压位置确实出现。六类故障和外部取消仍独立参数化，
各自保留 pytest 原生结果，不把一个 campaign 的结果复制到多个功能标签。

内部模型包含 81 项流水占用、flush、lookup、forwarding、主/子状态机、SRAM 初始化与仲裁、
refill/writeback 计数、early-response 标志、CPU burst 及错误检测和恢复要求。
`CacheInternalObserver` 在驱动稳定后和紧邻上升沿读取快照，核对寄存器变化和独立
metadata/data 写入账本。初始化明确覆盖 128 个 set，invalid-way 优先级明确覆盖
15 个非零掩码；四路 victim/release 与八种起始 word 各有独立 bin。
信号缺失和宽度错误会失败，未激活的属性保留未覆盖场景。

在仓库根目录构建模块级 DUT，运行所有项目回归：

```bash
PICKER_ROOT=/path/to/picker PICKER_BIN=/path/to/picker/build/bin/picker \
  examples/integration/cache/build_modules.sh CacheStage1 CacheStage2 CacheStage3
export CACHE_MODULE_DIR="$PWD/output/cache-modules"
python3 -m pytest -q examples/integration/cache/test_cache_coherence.py \
  examples/integration/cache/test_cache_internal.py \
  examples/integration/cache/test_cache_stage1.py \
  examples/integration/cache/test_cache_stage2.py \
  examples/integration/cache/test_cache_stage3.py \
  examples/integration/cache/test_cache_assertions.py
```

Stage1 检查组合 ready 真值表和地址分解；Stage2 单独激励正常填充无法达到的 invalid-way
组合及 forwarding 更新；Stage3 使用独立 SRAM 输入模型检查八拍 refill、writeback、
MMIO、release 和 burst 计数。完整 Cache 用并发 CPU/probe、连续命中、输出背压及
refill/writeback/release 期间复位检查流水和仲裁。内部产物与外部事务覆盖分开保存：
`internal-functional-coverage.json` 记录激活，`internal-checks.json` 记录比较和 `closed`。

重复 tag、MMIO 异常命中、metadata/data 写冲突和禁止 `flush[1]` 的负例在独立进程
检查 RTL assertion，不能只看进程非零退出。读 burst 反例保存于 `rtl-defects.json`：当前 RTL 后续拍重复 demand word，
该要求即使 bins 已激活仍未关闭；测试通过表示成功复现预期缺陷。检查器伪造快照另存
`checker-counterexample-coverage.json`，不贡献 DUT 功能覆盖。
具体激励见项目测试，采样条件见[属性观察器](cache_internal_coverage.py)，
设计义务与必需场景见[验证计划](verification_plan.json)。

完整回归预期 `47 passed`，其中四个 PASS 表示成功复现模块级及完整 Cache 的读 burst 缺陷。
81 项内部要求共 412 个必需场景 bins；实际合并激活 100%，80 项符合本基线关闭条件，
读 burst 数据要求保持 `closed=false`。CPU valid 连续性及其撤回反例、refill 重叠请求、
CPU/probe 并发与禁止 flush 负例均已加入。逐项状态由本次运行的 `internal-checks.json`
记录；报告、覆盖数据库和缺陷证据留在本地产物目录，不纳入 Git。

## 验证错误检测和恢复

错误条件也有验证义务。接口允许的 error 应正确传播；明确禁止的条件应触发指定检查；
内部状态故障按已规定的复位或恢复逻辑检查。规范没有定义的非法命令和自动恢复行为
保留为规格缺口。当前证据依据 RTL 断言、复位契约及明确清零逻辑，不代表完整架构规格。

| 实际注入 | 检查的行为 | 证据 |
| --- | --- | --- |
| 模块及完整 Cache 的重复 tag、MMIO 命中、metadata/data 写冲突、禁止 flush，共十种场景 | DUT 中错误条件成立；指定 assertion 和 fatal 源码位置；SIGABRT；分支计数大于零 | `assertion-witness.json`、`rtl-assertion.log`、`verilator-coverage.dat` |
| 完整 Cache LFSR 全零 | 下一拍回到 1，后续真实读访问正确 | `lfsr-recovery-witness.json` |
| 主状态 15、读取子状态 3、两者同时异常 | 执行故障状态后 reset 清空控制，metadata 重新初始化，无旧响应，重新 refill 的数据正确 | `illegal-state-reset-witness.json` |
| 命中响应受阻时 `needFlush=1` | 三个受阻周期保持；接受响应或 reset 后清零；响应/取消和后续读正确 | `faulted-flush-recovery-witness.json` |

这些 case 改变真实 DUT 寄存器或 RAM。例如完整 Cache 的重复 tag 先修改两路 metadata
RAM，再从 CPU 接口发起查找。它们没有伪造 Python 观察快照，也不标为合法 CPU 流量。
非法状态测试验证 reset 恢复；当前没有依据要求状态机自动恢复。异常 `needFlush` 的
清理验证现有实现，不表示当前可写配置允许 `flush[1]`。

assertion 回归在独立 Linux 子进程中预加载
[保存钩子](../verilator_fatal_coverage.cpp)。钩子调用已加载 DUT 自己的覆盖率写入函数，
然后继续调用原来的 fatal 处理。测试同时要求确切 SIGABRT 和指定分支非零计数，不会
通过禁用 assertion 或继续仿真来凑覆盖。钩子由 session fixture 使用 C++ 编译器构建，
当前支持 Linux ELF/libstdc++ 的 Verilator 导出 ABI；不匹配的库会在激励前明确失败。
钩子仅在 assertion 子进程内启用，生成库、日志和覆盖数据库留在 pytest 产物目录。

## 根据 RTL 未覆盖行补场景

行覆盖以生成 DUT 输出的 Verilator 数据库为依据。Python 框架测试的覆盖率单独统计。
Picker 的 `--coverage` 同时启用执行计数和 toggle；分析执行路径时保留 `v_line` 和
`v_branch`，按数据库中的 `S` 字段展开一个计数器代表的多行范围，交给
`verilator_coverage --write-info` 转换。另保留含 toggle 及生成封装的完整统计。
完整 Cache 与单独导出的 Stage1/2/3 按各自 hierarchy 分开报告，避免模块测试掩盖
顶层尚未执行的路径。

2026-10-02 使用同一份 RTL、同一生成 DUT 和 Verilator coverage 5.026 比较新增场景
前后，执行行覆盖从 **331/350（94.57%）提高到 336/350（96.00%）**；逐覆盖点从
**267/281 提高到 272/281**。分母及零计数点均保留。

| 新增真实场景 | 输出检查 | 新命中的 Cache.v 覆盖点 |
| --- | --- | --- |
| `flush[0]`：空流水、Stage2 受阻、输入同拍接受 | Stage2 清空；保留 Stage3 响应；被取消请求无响应；后续读正确 | 2836，覆盖范围包含 2837 |
| MMIO 读写请求各背压四周期 | 请求 payload 保持；未提前接受；只接受一次；响应和 byte-mask 写入正确 | 777 的等待分支 |
| 完整 Cache 读 burst，起始 word 0/7，输出背压 | 首字数据；状态/计数推进；八拍要求与实际响应数量比较 | 771、799、869，范围包含 772、800、870 |

完整 Cache 的 burst 首拍后 `valid_1` 提前清零，后续出现 coherence 输出，未完成八拍
CPU 响应。测试把这个预期缺陷写入 `rtl-defects.json` 和属性失败记录，不将该功能标为
关闭。两个既有模块级反例则检查后续拍重复 demand word。

2026-10-03 加入上述错误检测、恢复及终止前计数保存，剩余 9 个覆盖点全部命中。
同构建完整 Cache 的执行行覆盖达到 **350/350（100%）**，执行覆盖点 **281/281**，
其中 `v_line` 为 **85/85**，`v_branch` 为 **196/196**。含 toggle 的 DUT 源行统计为
**1238/1238**；包含 Picker 生成封装的原始范围仍为 **1288/1442（89.32%）**。
零计数行闭合来自实际执行的 DUT 场景，RTL、断言开关及排除项未改变。

| 闭合的 Cache.v 覆盖点 | 实际贡献 |
| --- | --- |
| 240 | 完整 Cache LFSR 全零故障恢复 |
| 262、876、900、924、948 | 完整 Cache 五类指定 assertion，终止前保存计数 |
| 2861 | 禁止 `flush[1]` 负例触发时的流水清空分支 |
| 787 | 异常 `needFlush` 在真实响应接受时清零 |
| 824 的 else | 读取子状态注入 3 后执行，再验证 reset 恢复 |

100% 只针对这份构建的已插桩 RTL 行和执行点；正常流量、故障注入及预期缺陷见证各有
单独的 case 和证据。读 burst 的已知错误仍保持未关闭。

对比工具 [rtl_line_coverage.py](../rtl_line_coverage.py) 保存逐点新增命中、贡献 case、
剩余源码位置、RTL hash、原始数据库列表和标准 LCOV。先在修改 case 前跑回归，保存
`--basetemp output/cache-before`；补完后跑到另一个目录，再执行：

```bash
python3 examples/integration/rtl_line_coverage.py \
  --before output/cache-before --added output/cache-after \
  --rtl "$PICKER_ROOT/example/CacheSignalCFG/Cache.v" \
  --top TOP.CacheSignalCFG_top --output output/cache-line-comparison
genhtml output/cache-line-comparison/execution-after.info \
  --output-directory output/cache-line-comparison/html
```

输入必须来自同一生成构建，工具会拒绝不一致的覆盖点集合。上述路径中的数据库、JSON
和 HTML 都是本地生成产物，由 `output/` 忽略规则覆盖。

同一配置的运行产物可按实际命中合并。下面 run-a/run-b 替换为两次执行的产物目录：

```bash
toffee-coverage-report \
  --functional run-a/functional-coverage.json \
  --functional run-b/functional-coverage.json \
  --output output/cache-combined.html --site output/cache-combined
```

## 独立的同步协议探针

普通序列多数在 `io_empty` 后发下一笔，它不能证明 early-response overlap 正确。
用纯同步 `StepHalf()` 的对照脚本单独验证：

```bash
python3 examples/integration/cache/cache_direct_protocol_probe.py \
  --artifacts output/cache-overlap
```

探针在所有输入驱动稳定后记录上升沿接受，保存 `direct-protocol-probe.json`。
旧探针在 refill 输入更新前登记 ready，造成“已接受但无响应”的误报；修正后第二请求
实际接受于 half-tick 382、响应于 410。`test_cpu_request_overlaps_remaining_refill` 同时
核对异步路径的真实接受、数据和 user。它们不能替代读 burst 的独立失败证据。
具体检查见[同步探针代码](cache_direct_protocol_probe.py)，每次运行的详细记录保存在指定产物目录。

`example_toffee.py` 另保留为短小的 trigger/backend 连通性检查；上述两个脚本分别承担
功能验证和协议差分；`test_cache_coherence.py` 集中保存 coherence 正常和故障回归，
不按每个 feature 拆文件。
