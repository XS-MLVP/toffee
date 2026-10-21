# 功能覆盖率与报告

功能覆盖率回答的是“哪些情况已经测到，哪些还没有”，不是“设计是否正确”。
先从规格列出需要观察的情况，再定义 bin，并在实际事件发生时采样。

可以对 Monitor 整理好的事务手动采样，也可以绑定信号和时钟，让 Execution 自动采集。
下面先介绍直接定义覆盖组的写法；需要复用较大模型时，见[类声明](coverage-classes.md)。
跨周期事件见[时序覆盖](coverage-patterns.md)，按编号统计延迟见[事务覆盖](coverage-transactions.md)。

## 定义 covergroup

```python
from xreactor import Bin, CoverGroupDef, CoverPointDef, CrossDef

definition = CoverGroupDef(
    "fetch",
    (
        CoverPointDef(
            "target",
            {
                "local": Bin.values("local"),
                "external": Bin.values("external"),
            },
            description="Exercises both fetch routes.",
        ),
        CoverPointDef(
            "response",
            {
                "ok": Bin.values("ok"),
                "error": Bin.values("error"),
            },
            description="Exercises success and error responses.",
        ),
    ),
    (
        CrossDef(
            "target_x_response",
            ("target", "response"),
            description="Checks responses on every route.",
        ),
    ),
    description="End-to-end fetch coverage.",
)

coverage = definition.instantiate("core0.ifu")
coverage.sample(
    {"target": "local", "response": "ok"},
    metadata={"seed": 7, "tick": 42},
    details=False,
)
coverage.sample({"target": "local", "response": "error"})
coverage.sample({"target": "external", "response": "ok"})
coverage.sample({"target": "external", "response": "error"})
coverage.assert_coverage(100.0)
```

`details=False` 累计计数与运行证据，不返回本次采样的 bin 命中明细。

环境可以给运行提供身份、上下文及事务采样规则，无需给 case 声明功能标签：

```python
coverage = definition.instantiate(
    "core0.ifu", run_id="ci-job-42/shard-1/run-7",
    run_metadata={"test": "test_fetch", "seed": 7, "rtl_build": "build-123"},
    contract="ifu.response-observed-before-scoreboard/v1",
)
```

不指定 run_id 时自动生成唯一身份。显式 run_id 应标识一次独立采集，不能只使用 seed，
因为相同 seed 可以运行多次。run_metadata 保存项目上下文，不自动校验 RTL/config 是否兼容；
需要阻止不同采样语义合并时，将其稳定名称与版本纳入 contract。

普通 point/cross 命中以 `provenance[bin_id][origin_id]` 保存该来源的 count、首次和末次
metadata，不保存全量事务。`origins` 关联 run_id、实例和 run_metadata。手动 sample 和
Python 收集器 可以保存实际命中位置；原生计数器 当前只保存运行关联和计数，
first/last 为 null，不把同步时间当成命中时间。未传 metadata 的普通命中也以 null 表示。

## Bin 类型

```python
Bin.values(0, 1)
Bin.transition(0, 1, 2)
Bin.range(0, 15)
Bin.ranges((0, 3), (8, 11))
Bin.masked(value=0b1000, mask=0b1100, width=4)
Bin.array("bucket", 0, 255, count=16)
Bin.default()
Bin.ignore(Bin.values(7))
Bin.illegal(Bin.range(12, 15))
```

`at_least=N` 要求 bin 至少命中 N 次。`ignore` 不进入覆盖率分母；`illegal` 默认立即
抛出 `IllegalBinError`，也可以实例化时选择 record policy。定义中的 `description`
会进入 JSON 和 HTML 报告，不进入 sampling 热路径。

## 相邻转移

`Bin.transition(0, 1, 2)` 要求连续三个 sample 分别为 0、1、2。手动 `sample()` 时，
“相邻”指相邻调用；绑定时钟时，指相邻的指定采样阶段。中间插入其他状态不会命中。

默认允许滑动重叠：对 `Bin.transition(1, 1)` 连续采样三次 1，命中两次。
`overlap=False` 则在完成后从下一个 sample 重新开始，只命中一次。`at_least` 始终是
累计命中目标；例如三拍保持可写 `Bin.transition(1, 1, 1)`。

门控屏蔽和依赖字段的 X/Z 会断开该点的转移历史。ignore bin 只影响分类，不能删除
时间：`1, 9, 2` 即使忽略 9，也不会命中 `1→2`。未知数据记录在点的 `unknown` 统计中。

## 绑定信号，由 Execution 管理

```python
from xreactor import Execution, RisingEdge

states = CoverGroupDef("states", (
    CoverPointDef("state", {
        "busy": Bin.values(1),
        "complete": Bin.transition(0, 1, 2),
    }),
)).instantiate("core0.pipeline")

states.bind(
    trigger=RisingEdge(clock),
    fields={"state": dut.state},
    strategy="native",
)

async with Execution(backend, coverage=[states]):
    await run_case()
    checkpoint = states.report()

CoverageDatabase([states]).write_json("states.json")
```

`bind()` 返回当前 CoverGroup，不创建新的公开组件。`fields` 的键对应 point 的
`source` 和 Iff 的字段路径。原生支持 XData 的完整无符号位宽，包括超过 64 位的
值、范围、mask、转移、Iff 和同样本 cross。计数器仍为检查溢出的 64 位整数，
与被观测信号的位宽无关。整个 group 一起选择执行路径：

- `native`：不支持的输入在启动时报告错误；
- `python`：明确逐 sample 回到 Python；
- `auto`：优先原生，否则整组回退。报告的 `sampling_backend` 和
  `sampling_fallback` 给出实际路径及原因。

采集本身不会推进时钟；用例仍需等待 Trigger 或执行已有的事务操作。多个 group
分别拥有计数与历史；同一 group 不能同时归属两个 Execution，也不能在运行时手动
`sample()` 或 `merge()`。后端必须在所属 Execution 退出后才能关闭。
已经通过 Execution 采样的组也不能在退出后混入手动 sample；事务采样请使用独立实例，
避免同一份统计混用周期与事务两种含义。

宽数据的定义方式相同，例如观测 128 位 payload 的高位：

```python
high = 1 << 100
payload_bins = CoverPointDef("payload", {
    "exact": Bin.values(high + 1),
    "range": Bin.range(high, high + 3),
    "mask": Bin.masked(high + 1, high + 3, width=128),
    "path": Bin.transition(high + 1, high + 2),
}, overlap="allow")
```

匹配和非法命中的诊断值均保留高位。任意位出现 X/Z 时，该 point 增加 `unknown`，
跳过普通命中并打断相邻转移；mask 的不关心位也不例外。非法值在命中当时保存，
之后修改信号或延迟 `sync()` 不会改变诊断证据。超过信号取值域的无符号常量不会被
截断：不可达的 value/transition 不命中，范围和 mask 按原始整数语义匹配。
带符号值、宽信号算术表达式不在此次扩展范围，原生仍要求直接绑定 XData。

运行完整示例（两次运行的 bins/counts 应相同）：

```bash
python3 examples/coverage/functional_coverage.py --wide
python3 examples/coverage/functional_coverage.py --wide --native --output wide.json
```

## 不同 Execution 的隔离与同步

默认每次进入 Execution 都清空 group 的计数和匹配历史。需要保留先前已完成的命中时，
在绑定时设置 `accumulate=True`：

```python
states.bind(trigger=RisingEdge(clock), fields={"state": dut.state},
            strategy="native", accumulate=True)
for scenario in scenarios:
    async with Execution(backend, coverage=[states]):
        await scenario()
```

即使累计统计，匹配历史仍按 Execution 隔离：前一次结尾的 0、1 不能与后一次开头的 2
拼成 `0→1→2`。分别保存每次运行再合并时，应使用默认隔离；不要将多次累计快照当作
不同运行相加。

| 操作 | 计数 | 未完成的匹配 |
| --- | --- | --- |
| `report()` / `sync()` | 从原生同步，不重复累计 | 保留 |
| `clear_history()` | 保留 | 清空 |
| `reset()` | 清空，同时更新原生统计 epoch | 清空 |
| Execution 退出 | 同步最终数据并保留 | 清空并释放原生注册 |
| 下次 Execution 进入 | 默认清空；`accumulate=True` 保留 | 始终重新开始 |

运行中读取 `samples` 等直接统计字段前调用 `sync()`；`report()`、覆盖率查询及 JSON
导出自动同步。同步应在 Execution 所在线程执行；退出后可安全读取最终报告。
正常退出、用例异常、外部取消及部分启动失败都会释放注册。快照失败仍继续清理，
同时保留原用例异常。

## 用已有 Trigger 识别复杂模式

```python
from xreactor import Next, Sequence, Wait, xtrigger

@xtrigger(sample=RisingEdge("clock"))
def start_then_done(dut):
    return Sequence(Wait(dut.state == 1), Next(dut.state == 2))

states.bind(trigger=start_then_done(dut), fields={"state": dut.state},
            strategy="native")
```

此时完整模式每次完成才采样一次 group，字段取完成阶段的当前值。Bin.transition 的
时间单位也随之成为相邻的模式完成事件。已有 Within、Hold、FSM 同样可作为采样源；
Next 要求紧接的一次 sample 成功，失败即重新等待起点。Sequence/FSM 默认仍为非重叠
单实例；覆盖绑定可显式允许有容量上限的重叠。普通 `await trigger` 的语义不变。

表达式 Trigger 的 `enter/each_sample/change` 按声明生效。持续为真的条件使用 `enter`
时只在进入时采样，使用 `each_sample` 时每个有效 sample 都采样。

项目可显式传入 `abort=dut.abort_flag`，并把它放入 fields 的具名映射；abort 优先于
同拍完成，清历史但保留计数。框架不推断信号的协议含义。`@pytrigger` 需要
`contract="项目定义名称/v1"` 来标识自定义判断语义，始终走 Python 采样；其自定义闭包
状态不由框架管理。采样规则不同的运行不能合并或继续累计。

现有示例可直接运行：

```bash
python3 examples/coverage/functional_coverage.py --stateful
python3 examples/coverage/functional_coverage.py --stateful --native --output state.json
```

原生示例要求含 `XTriggerEngine.CoverageVersion() == 5` 的 xcomm Python 绑定。
修改 C++ 后端后需同步重建 绑定；旧版本下 `native` 在启动时失败，`auto` 整组回退。

## 可选的并发匹配

前一次模式尚未结束时还需要观察新的起点，可以在已有绑定上开启重叠：

```python
from xreactor import Sequence, Wait, Within, xtrigger

@xtrigger(sample=RisingEdge("clock"))
def completion(dut):
    return Sequence(Wait(dut.start), Within(1, 3, dut.done))

group.bind(
    trigger=completion(dut), fields={"value": dut.value},
    overlap=True, max_active=8,
    diagnostics="summary",  # 独立开关；可以省略
)
```

- `overlap=False` 为默认值。已有匹配期间不接受新起点，完成周期也不复用为新起点。
- `overlap=True` 必须显式指定正整数 `max_active`；只适用于编译的 Sequence/FSM。
  重叠 Sequence 以 `Wait` 开头，每次采样起点为真都可以启动一次；不自动改为边沿检测。
- FSM 从 start 状态进入其他状态时启动一个实例；start 自环不创建实例。
  start 直接触发终态也算一次完成。活动实例回到 start 且没有触发终态时结束该次尝试。
- 每个实例每次采样最多推进一步。先处理已有实例，再接收新起点；同拍完成能释放容量。
  旧实例的完成不让新实例在同拍再前进一步。
- 达到容量后又出现新起点会报错，报告标记 `collection_complete=False`；不静默忽略。
  已有计数保留，`assert_coverage()` 拒绝用不完整采集宣告达标。
- 配置只能在 Execution 外修改；并发策略进入采样规则，不同策略的产物不能直接累计或合并。

例如第 1、2 拍 start，第 3 拍 done，在上述窗口内会完成两次模式；group 也采样两次。
两个实例共享同一个完成条件，不代表框架已把两笔事务与各自响应配对。按 key 区分事务、
保存起点字段的 capture 尚未提供；这类场景仍使用项目 Monitor/Scoreboard 的关联结果。
字段取完成阶段的值，时序 bins 的 sample 单位为每次模式完成事件，可能同拍出现多次。

运行完整示例：

```bash
python3 examples/coverage/functional_coverage.py --overlap
python3 examples/coverage/functional_coverage.py --overlap --native --output overlap.json
```

## 可选的匹配诊断与按需查看

`diagnostics="off"` 为默认值，只保留原有覆盖计数及非法命中证据。
`diagnostics="summary"` 为复杂采样模式和 `Bin.transition` 增加以下汇总，不保存逐拍轨迹：

| 字段 | 含义 |
| --- | --- |
| `started` / `completed` | 已启动 / 已识别完成的匹配次数 |
| `failed` | Next 失配或活动 FSM 返回起点而未完成 |
| `expired` | Within 超过窗口上界而未完成；上界内成功优先 |
| `aborted` | 绑定的显式 abort 条件终止的活动匹配数 |
| `cleared` | clear_history、bin gate 或未知样本清除的活动历史数 |
| `unfinished_at_close` | Execution 退出前仍未完成的匹配数 |
| `peak_active` | 活动匹配数峰值 |

`completed` 描述匹配器识别，不保证该次最终进入普通 bin：group gate、ignore/illegal
优先级仍独立生效。诊断不参与覆盖率分母，未完成或过期也不会自动成为 checker 失败。
HTML 报告只在收集了 summary 时提供折叠区域，JSON 在 `diagnostics.patterns` 中保存统计。

运行中可以随时查看现有状态，无需开启 summary：

```python
async with Execution(backend, coverage=[group]):
    await ClockCycles(clock, 2)
    progress = group.inspect()
    # {"tick": ..., "phase": ..., "trigger": [...], "bins": {...}}
    # Sequence/transition: step（从 0 起）、age、held；FSM: state 名称。
```

`inspect()` 在 Execution 所在线程读取，不推进时钟、不改变匹配历史，返回独立数据。
它只在 Execution 活跃期间可用；退出后查看 `report()` 中的汇总。退出前保存未完成数量，
随后释放全部状态，不把普通关闭或外部取消计入业务 `aborted`。

默认下次 Execution 重置统计；`accumulate=True` 保留计数及诊断，但不继承匹配历史。
汇总合并对事件次数求和、对峰值取最大值。诊断开关不改变采样规则；混合开启和关闭的运行时，
报告通过 `collected_runs` / `uncollected_runs` 标明诊断范围，未采集不能冒充零。
`clear_history()` 保留统计并记录清除数量；`reset()` 同时重置覆盖计数、诊断和历史。

## 保存和合并

```python
from xreactor import CoverageDatabase

database = CoverageDatabase([coverage])
database.write_json("functional-coverage.json")

merged = CoverageDatabase.read_json("run-a.json")
merged.merge(CoverageDatabase.read_json("run-b.json"))
merged.write_json("merged.json")
```

只有兼容 schema digest 和 sampling contract 的同名实例才能安全合并。每份报告携带
采集来源与 snapshot_id；重复来源、相同实例的重复 run_id、累计快照之间的重叠都会报
CoverageMergeError，计数保持不变。应合并独立运行的最终快照，不能把同一运行的中间
快照和最终快照相加。`accumulate=True` 保留采集来源，以便检测重叠；reset 会开启新来源，
使用显式 run_id 时应实例化新的运行并换用新的 run_id。

数据库 JSON 版本为 2，schema 自身版本独立。Cross 的 counts 使用无歧义的 JSON 数组
编码作为 ID，`bins` 保存 id、tuple、label、kind 与 at_least；label 只用于显示。
读取接口只接受当前报告格式，不提供旧报告转换。

## 统一 HTML 报告

Verilator coverage 先转换为 LCOV，其他能产生 LCOV 的 simulator 使用同一入口：

```bash
xreactor-coverage-report \
  --functional functional-coverage.json \
  --line-coverage Verilator=line-coverage.info \
  --site coverage-report
```

输出包含：

- functional covergroup/point/cross 分级页面；
- 每个 bin 的目标、命中次数和描述；
- genhtml 生成的逐文件、逐行 RTL coverage；
- functional 与 line coverage 的统一导航。

完整样例见
[functional_coverage.py](https://github.com/Makiras/xreactor/blob/main/examples/coverage/functional_coverage.py)。

## 从事务字段采样并设置门控

`source` 支持点分路径，读取 mapping、dataclass/对象属性和 BundleValue。定义复用时
把设计字段名和覆盖率名称分开；Iff 指定何时计数：

```python
from xreactor import Iff

point = CoverPointDef(
    "opcode", {"read": Bin.values(0), "write": Bin.values(1)},
    source="request.opcode", iff=Iff.equals("accepted", True),
    weight=2, goal=100,
)
```

Iff 可放在 CoverGroupDef、CoverPointDef 或 CrossDef 上，分别控制整次采样、单点或交叉。
`Iff.not_equals()` 表达排除条件，`Iff("mode", (1, 2))` 接受多个值。门控次数在报告的
`gated` 字段中可见；缺失字段是采样错误，不静默跳过。

## 控制交叉数量与重叠

默认 cross 对各 point 的正常 bin ID 取笛卡尔积。组合很多时指定 include：

```python
cross = CrossDef(
    "operation_result", ("operation", "result"),
    include=(("read", "ok"), ("write", "ok")),
    ignore=(("read", "retry"),),
    illegal=(("write", "forbidden"),),
    at_least=2, max_auto_bins=64,
)
```

这些字符串必须是相关 point 中定义的 bin 名字，而非原始输入值。max_auto_bins 限制
自动展开的正常交叉数，过大在定义阶段报 CoverageSchemaError，避免运行中膨胀。
point 的 `overlap="warn"` 默认诊断重叠，`"error"` 拒绝重叠定义，`"allow"` 明确允许。
允许重叠时，一次采样可命中多个 bin，cross 也会记录所有命中组合。

判定优先级为 illegal、ignore、normal，default 记录其余值；只有正常 bin 进入覆盖率
分母。非法命中以 IllegalHit 保存 metadata；即使选择 `illegal_policy="record"` 延后
报错，`assert_coverage()` 仍会拒绝含非法命中的运行。

普通 bin 的全部取值被 ignore/illegal 排除时，它保留在 schema 和诊断计数中，但从
分母、uncovered 和自动 cross 空间移除；报告的 excluded_bins 给出原因。部分排除仍
保留 bin，例如 `{0,1}` 排除 1 后必须由 0 命中。区间和 mask 的联合排除无需枚举值域；
分析过于复杂时在定义阶段报错。Transition 的排除按本框架的完成时优先级处理，包括
完成值被静态排除或被相同模式完全覆盖的情况，不表示支持完整 SV transition 运算符。
排除后没有 eligible normal bin 的 point/cross 属于无效定义。

## 判断目标、查缺口与复用定义

- `point_coverage(name)` / `cross_coverage(name)` 查看局部百分比。
- `coverage` 是 point/cross 正常覆盖率按 weight 加权的结果，weight=0 仍采样但不计权重。
- `goal_met` 只表示组的原始加权百分比达到 group.goal；每项报告也有独立 goal_met。
- `covered` 要求组目标及所有正权重 point/cross 的目标达成、采集完整且没有非法命中。
  它表示覆盖验收状态，不代表 Scoreboard 或整个测试通过。
- `assert_coverage()` 检查同样的条件；minimum 覆盖组阈值，子项仍使用各自 goal。
  只需组阈值的调用可以显式设置 `per_item=False`，仍检查采集完整性和非法命中。
  这是相较旧版默认验收行为的变化；百分比计算没有按 goal 归一化。
- `uncovered()` 返回尚未达到 at_least 的 bin 路径。达到一半命中目标不会得到半个 bin 的覆盖分。
- `sample()` 默认返回 CoverageSample（point_bins、cross_bins、illegal_hits）；details=False
  适合只需累计计数的热路径。
- `reset()` 清空这个实例的计数和非法记录，保留定义；跨运行通常实例化新 group，避免混淆产物。

同名实例合并要求 definition digest 和策略一致，否则 CoverageMergeError。不同实例
保持各自结果，`CoverageDatabase.type_reports()` 给出同类型合计视图。JSON 往返会保留
定义、计数和描述；不要靠改名掩盖不兼容的覆盖计划。

## 单文件报告与 Python 集成

只需要分享一个文件时：

```bash
xreactor-coverage-report --functional functional-coverage.json --output coverage.html
```

Python 的 `generate_unified_coverage_report(functional_paths=[...], output=...)` 同样生成
自包含 HTML；`generate_unified_coverage_site(..., output_dir=...)` 生成站点。只有需要
LCOV 源码行页面的站点才依赖 genhtml；单文件报告及仅 functional 的站点不要求该工具。
站点输出必须为空或已有 XReactor 站点标记，不覆盖其他非空目录。
LCOV 中引用的源文件必须在生成报告的环境中可读；框架交由 genhtml 决定显示路径，
不会通过强行剥离目录前缀改变源码查找。

已有数据模型可用 `parse_lcov()`、`build_unified_coverage_model()` 和
`render_unified_coverage_html()` 分别完成解析、组合与渲染。functional bins 与 simulator
行覆盖率分别展示，不平均成一个“验证覆盖率”；pytest 通过/失败使用原生结果。
完整保存配方见[运行产物](verification-flow.md)，覆盖建模与 case 设计见
[功能点与跨用例覆盖](functional-points.md)。
