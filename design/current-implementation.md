# XReactor 当前实现说明（代码审计版）

> 状态日期：2026-10-07（历史测量和回归数量仍按各节的验证日期保留）
> 事实来源：`src/xreactor/`、CI 固定的 xcomm 提交与可运行示例。
> 本文描述“已经能运行什么”，不把 Roadmap 中的规划当作现有能力。

[asyncio 调度观察器](architecture/asyncio-observer.md)已接入公共 Execution，统一
处理原生 asyncio 任务与同步原语的阶段交接。生产 Driver 使用原生 Lock/Semaphore；
external_task 和 max_settle_rounds 是公共接口，实验子类已移除。

普通测试以 `ClockCycles` 和事务提交表达时序，边沿与 `DriveStable` 由协议组件管理；
故障注入等需要精确阶段的测试仍可直接使用边沿。Driver 已包括同步和后台提交两种
单周期、固定阶段多周期模板，`DriveStage` 声明固定阶段时长；动态握手由项目 Driver
定义接受条件。当前层次与边界见[Driver 指南](../docs/guides/drivers.md)。

版本构建已接入 tag 派生的包版本、PR 版本标签、固定 native 依赖与发布前完整 CI。
源码包重建、隔离安装和产物清单检查是发布门槛；流程见
[版本与发布](../docs/guides/versioning-and-releases.md)。本地验证与 GitHub 实际运行的
结果分别记录，不能把工作流文件已经存在视为远端 CI 已通过。

## 结论

当前已经完成一个可运行的**单时钟、asyncio-native 核心 MVP**，但没有完成整个
M0～M6 Roadmap。

已形成端到端闭环的是：

```text
await XTrigger
  -> XReactor Registration + asyncio.Future
  -> XCommClockBackend
  -> C++ XTriggerEngine 在 stable phase 求值
  -> 一批 XBackendHit
  -> typed XEvent
  -> 恢复等待它的 asyncio Task
```

Expr、Sequence 和分支 FSM 已经能在 C++ 中逐 sampling phase 求值；普通 HTTP、
Queue 和其他 asyncio task 与 SimulationPump 共用宿主 event loop。尚未完成的主要
部分是多时钟共同时间模型、单次长 Execution 内的 native program 回收、zero-copy hit、signed/cast IR、
GIL release/owner thread 和完整第三方兼容矩阵。框架级 functional coverage 核心与
真实 e203 memory-direct 验证闭环也已经完成。

## 代码组成

| 层次 | 当前实现 | 职责 |
| --- | --- | --- |
| Python Trigger | `triggers.py`、`decorators.py` | awaitable 规格、`@xtrigger`、`@pytrigger` |
| Python IR | `ir.py` | Expr、Sequence、分支 FSM 的不可变描述 |
| Reactor | `reactor.py` | Registration、Future、广播、取消、订阅队列 |
| 调度 | `execution.py` | RunUntil batch、wall quantum、pause、asyncio yield |
| asyncio adapter | `external.py`、`combinators.py` | Event/Queue/Task/WallTimeout、AnyOf/AllOf |
| 协议 primitive | `protocols.py` | falling drive、ready backpressure、单 rising 接受 |
| 数据/接口 | `data.py`、`interfaces.py` | Bundle、四态快照、ReadyValid role/fire |
| 方法学组件 | `components.py`、`sync_drivers.py`、`async_drivers.py`、`monitors.py` | Driver/Monitor 契约、提交模板、ownership、原子 capture |
| 功能覆盖 | `coverage.py`、`coverage_report.py` | CoverGroup/Point/Bin/Cross、LCOV 与统一 HTML |
| 测试 backend | `MemoryBackend` | Python 语义模型，不代表 simulator 性能 |
| native bridge | `XCommClockBackend` | IR lowering、SWIG 批量转换、program cache |
| C++ 热路径 | `xspcomm/xtrigger.h`、`xtrigger.cpp` | watcher、phase 求值、FSM state、hit batch |
| 时钟基础 | `xclock.h`、`xclock.cpp` | StepHalf、half tick、stable phase |

`XTrigger` 是不可变等待规格；每次 await 创建独立 `Registration`。运行状态属于
Registration/C++ watcher，而不属于 Trigger。`XEvent` 是已经发生的结果，不是
可以再次 set/wait 的同步原语。

## 最小使用方式

```python
from xreactor import (
    FallingEdge,
    RisingEdge,
    Execution,
    Value,
    XCommClockBackend,
)


async def run(dut):
    # 新生成 DUT：clock 是直接 XData；GetXClock() 是其调度器。
    clock = dut.GetXClock()
    backend = XCommClockBackend(clock)
    async with Execution(
        backend,
        default_sample=RisingEdge(dut.clock),
        quantum_ms=10,
    ):
        await FallingEdge(dut.clock)
        dut.req.value = 1

        # 保证在下一 rising stable phase 判断，不在任意 Python 时刻判断。
        ready_event = await Value(dut.ready, 1)
        assert ready_event.phase.name == "RISING_STABLE"
```

`FallingEdge` 命中后，Pump 会先返回 asyncio，使同步写入有机会发生，再推进下一
个 Rising。`Value`、`ValueChange`、`@xtrigger` 和 `@pytrigger` 必须显式指定
`sample=`，或使用 Execution 的 `default_sample`；框架不猜测默认时钟。

## 已实现 Trigger

| Trigger/API | MemoryBackend | XCommClockBackend | 当前语义 |
| --- | :---: | :---: | --- |
| `RisingEdge` / `FallingEdge` | 是 | 是 | stable half-step barrier |
| `ClockCycles` | 是 | 是 | 只计算 rising stable phase |
| `Value` | 是 | 是 | 指定 sample 的无符号相等条件 |
| `ValueChange` | 是 | 是 | 以注册时值为 baseline |
| `SimTimeout` | 是 | 是 | simulation cycle，不是 wall time |
| `@xtrigger` Expr | 是 | 是，C++ 求值 | `&`、`\|`、`~`、六种比较 |
| `Sequence` | 是 | 是，C++ FSM | `Wait`、`Within`、`Hold` |
| 显式 `FSM` | 是 | 是，C++ FSM | 有序分支、goto、terminal trigger |
| `@pytrigger` | 是 | 是 | 每个指定 sample 回 Python 调同步 predicate |
| `WallTimeout` | asyncio | asyncio | 宿主 wall-clock timeout |
| `AsyncioEventTrigger` | asyncio | asyncio | 等待已有 asyncio.Event |
| `QueueTrigger` | asyncio | asyncio | event.value 携带取出的元素 |
| `TaskComplete` | asyncio | asyncio | shield 原 task，取消 waiter 不取消原 task |
| `AnyOf` / `AllOf` | asyncio | asyncio | 支持 simulation/external 混合等待 |

表达式的 `& | ~` 当前是逻辑组合，不是任意位运算表达式。Python 的
`and/or/not`、隐式 truth test 和链式比较会被拒绝，且不会自动降级成 pytrigger。

## Condition 发射模式

`Value`、Expr `@xtrigger` 和 `@pytrigger` 支持三种模式：

- `enter`：默认，只在 false 到 true 时发射；
- `each_sample`：每个为 true 的 sample 都发射；
- `change`：false/true 两个方向都发射，`event.value` 是变化后的布尔值。

新 Registration 的前值从 false 开始。因此条件注册时已经为 true，也会在下一个
明确 sample 发射一次，而不是在注册调用栈中立即返回。persistent subscription
rearm 时保留前值，所以持续高电平不会被默认 `enter` 每周期重复投递。

Sequence/FSM 本身只产生离散 terminal event，不接受非默认 Condition mode。

## FSM 与订阅

```python
from xreactor import FSM, RisingEdge, State, on, xtrigger


@xtrigger(sample=RisingEdge("clk"))
def request_ack(dut):
    return FSM(
        start="WAIT_REQ",
        states={
            "WAIT_REQ": State().when(dut.req).goto("WAIT_ACK"),
            "WAIT_ACK": (
                State()
                .when(dut.flush).goto("WAIT_REQ")
                .when(dut.ack).trigger("ACK")
            ),
        },
    )


@on(request_ack, delivery="lossless", capacity=64)
async def observe(event):
    scoreboard.push(event)


subscription = sim.subscribe(observe.bind(dut))
```

FSM 实例状态按 watcher 隔离。persistent FSM 采用非重叠语义：terminal phase
完成整批广播后 reset，从下一个 sampling phase 开始新一轮，不把 terminal phase
同时当成下一轮起点。

`@on` 不创造另一套 Trigger；它创建持久 Registration 和独立串行 handler task。
backend 在 handler 执行前 rearm，因此没有“handler 完成后才重新 await”的空窗。
可选同步 `capture(event)` 在 publish barrier 内取得不可变数据，再把结果交给 async
handler；capture 不允许 await。

- 默认 `lossless`，容量 64；
- 队列满抛 `SubscriptionOverflowError`，Execution 失败；
- `latest` 必须显式选择，满时替换旧事件；
- handler exception 默认使 Execution 失败；
- close 会 cancel 并 await 框架创建的 handler task，不取消外部 task。

## phase、batch 与 asyncio

当前有四个公开 phase：

- `FALLING_STABLE`；
- `DRIVE_STABLE`；
- `RISING_STABLE`；
- `EXTERNAL`。

每个 XClock half-step 内完成 clock pin、eval/write/eval/refresh 后才执行 TriggerEngine
求值。一个 phase 中所有 watcher 都扫描完，再整体返回 hit batch。

`DRIVE_STABLE` 是按需执行的同 half-tick barrier：FALLING_STABLE 广播后，Pump 让被
唤醒的 coroutine 执行到各自下一次 await；若此时存在 DriveStable watcher，则 backend
刷新组合逻辑并调用 native `SamplePhase(DRIVE_STABLE)`，同步 capture 完成后才推进
RISING_STABLE。它不等待 HTTP 或任意外部 task，也不冒充 ReadWrite/ReadOnly region。
backend 通过 `capabilities.drive_stable` 明确声明支持；不使用该 phase 的旧 backend
保持原有调用流程。

SimulationPump 默认：

- `max_batch_ticks=4096`；
- `quantum_ms=10`；
- `budget_check_interval=64`；
- `max_settle_rounds=100_000`。

C++ RunUntil 在命中、edge barrier、run limit、backend stop 或 wall quantum 到期时
返回。每次返回后 Pump 都执行一次 `await asyncio.sleep(0)`，让 HTTP、pytest task、
取消和其他 coroutine 获得运行机会。推进和 DriveStable 采样前，还需等待本 Execution
的即时回调及其级联唤醒处理完；超出预算抛出 SimulationNotSettledError。

外部 await 不会自动暂停仿真。只有存在 simulation Registration/subscription 时
Pump 才推进；如果需要明确冻结，使用：

```python
async with sim.paused():
    response = await http_client.post("/configure")
```

框架使用当前 running loop，不调用 `loop.stop()`/`loop.close()`，也不取消不属于
框架的 task。当前实现不创建 simulator owner thread；RunUntil 在 event-loop 线程
执行，并用 wall quantum 控制最长占用时间。simulator kernel 内部可以多线程，但
其对外 StepHalf 必须仍表现为原子的 stable phase barrier。

## Event、Hit 和取消

C++ watcher handle 是 `slot + generation`。slot 可复用，generation 用于拒绝取消
之后到达的旧 hit。one-shot Future 被取消时，Reactor 会 disarm 对应 watcher。

同一个 native edge occurrence 的多个 waiter 共享 event id，Reactor 因而交付同一
Python Event 对象。条件 watcher 的 mode、baseline 和 FSM 状态属于各自 Registration，
因此各自产生 event id；框架只保证同 phase 完整返回所有 hit，不尝试把“相似条件”
猜测为同一个 occurrence。

`BackendHit` 当前通过一个 SWIG vector 批量返回，不进行逐 hit 跨语言 callback；
但 vector 到 Python tuple 仍会复制，因此还不是 zero-copy view。

## 数值和 X/Z

native 热路径支持任意位宽无符号值：

- 1～64 位使用标量 ABI，更宽信号使用 little-endian byte-vector ABI；
- 负常量和超过目标 signal 位宽的常量拒绝；
- 没有隐式截断和隐式 signed cast；
- Value/Expr 条件依赖 X/Z 时，该 sample 不命中；
- 未知 sample 不更新 `enter/change` 的布尔前值；
- ValueChange 对所有位宽比较完整 `(aval, bval)`，能区分 0、1、X、Z 的变化。

已知 ValueChange 的 `event.value` 是 int；含 X/Z 时返回
`LogicValue(value=aval, x_mask=bval, width=...)`。

## backend 与线程所有权

Execution 启动时要求 backend 声明 `BackendCapabilities`，并至少保证：

- half-step；
- stable sample。

当前 MemoryBackend 和 XCommClockBackend 均声明 `thread_safe=False`、
`reentrant=False`。同一个 backend instance 由 lease 限制为只能被一个 active
Execution 使用；不同 backend instance 可处于同一个 asyncio loop/context 中。

ReadWrite、ReadOnly、NextTimeStep 不属于框架 capability，也没有对应公开 Trigger。
它们解决的是 Python 被动嵌入事件驱动 simulator 时的 region 协调，而本框架由 XClock
主动执行 pin update、eval、edge write、再次 eval 和 refresh，并在完成后发布 stable
phase。各 simulator adapter 必须满足这个 XClock 契约，不向上层暴露内部 event region。

## 已验证内容

当前回归覆盖：

- falling 后写入早于下一 rising；
- 同 edge 广播、完整 phase hit、slot generation、cancel；
- Value/default sample/ValueChange/XZ；
- Condition enter/change、native false payload；
- Expr、Sequence、分支 FSM、program cache 和 state isolation；
- pytrigger sample/rearm；
- AnyOf/AllOf、Event/Queue/Task/WallTimeout；
- `@on` rearm、lossless overflow、latest、handler exception；
- 真实 `asyncio.start_server` HTTP 请求期间仿真继续；
- 显式 paused 冻结；
- backend instance 共享被拒绝；
- close 不接管宿主 event loop。
- covergroup/point/bin/cross、illegal/ignore/default、`at_least`、schema digest 与 merge；
- e203 IFU-to-ICB 参考模型、背压/错误注入、事务超时、functional 和 RTL line coverage。

验证命令：

```bash
cmake --build /tmp/xreactor-xcomm-engine-build --parallel 64
ctest --test-dir /tmp/xreactor-xcomm-engine-build/tests \
  --output-on-failure -j 64

PYTHONPATH=src:/tmp/xreactor-xcomm-engine-build/python \
python3 -m pytest -q

examples/integration/e203/build_xreactor.sh
python3 -m pip install -e '.[test]'
examples/integration/e203/run_pytest.sh
```

此外，`example/CacheSignalCFG` 已用 Picker `--rw mem_direct` 重新生成并实际运行：新生成
端口直接为 `XData`，并报告 `XDataBackendKind_MemDirect`；`FallingEdge(clock)`、
`Value(ready)`、Expr `@xtrigger` 和 ReadyValid `fire` 均在 native 路径工作。`XPin`
与 `XData.xdata` 已作为本次 breaking change 删除，生成 DUT 直接暴露 XData。生成 DUT
同时提供 `dut.io.in_.req.bits.addr` 一类只读结构视图，
并与扁平属性保持同一 XData identity；顶层 `dut.io` 仍是 XPort，以保留批量 backend
API。协议不从名字推断，而由调用方通过 `ReadyValid.bind_tree()` 显式声明。smoke 脚本为
`examples/integration/cache/example_xreactor.py`。

Cache 功能验证器 `examples/integration/cache/cache_functional_xreactor.py` 进一步提供真实
refill/writeback/MMIO responder 和参考模型，覆盖 read miss/hit、masked write、
write allocate、4-way dirty eviction、backpressure 及冲突密集随机序列。三个 seed、
每个 300 次随机操作的数据核对均通过。早期 MMIO 重复请求来自 testbench valid 跨过
两个 rising edge；overlap 丢响应误报来自同拍 refill 输入更新前登记 ready。两者已修正，
Driver 和同步探针均在全部驱动稳定后判断接受。当前未关闭的 RTL 属性是 CPU read burst
后续数据重复 demand word，独立反例保留在运行产物中。使用与回归入口见
[Cache 示例说明](../examples/integration/cache/README.md)。XPin 删除决策、XData native identity 和时钟域校验见
`backend/xpin-xdata-boundary.md`。

Cache 验证期间发现手写 ready/valid driver 很容易让 valid 意外跨过两个 rising edge，
因此框架新增 `drive_ready_valid()`。它复用当前 falling-stable phase、调用 backend
组合刷新、在 backpressure 时保持 payload，并在首个 accepting rising 后撤销 valid；
取消也会清理 valid。语义和边界见 `features/ready-valid.md`。

e203 IFU-to-ICB 环境使用独立参考模型核对 0/1/2 次 ICB command、路由、地址、
指令数据和 error OR。2026-09-29 更新后，34 笔定向加 500 笔随机的默认 seed 及另外三个
seed 均通过；每组 534 笔全部经 Scoreboard 检查，15 个 point/4 个 cross 达到 100%。
默认组 RTL line coverage 为 199/300（66.33%）。原测试文件现有 23 项检查，包含不同
数据模式、截止周期、故障注入、超时恢复和取消清理。详见
[e203 示例说明](../examples/integration/e203/README.md)。

functional sampling 采用预编译 source path/exact-bin/cross set 和 `details=False` 快路。
早期 e203 相同流量的 9 组 on/off A/B 中位 slowdown 为 5.32%，并提供可失败的 CI 门槛；
pytest 同时产生自包含 functional + LCOV line coverage HTML。详见
`features/coverage-performance-and-reporting.md`。

2026-09-16 的历史结果为仓库 CTest 24/24、native-backed Python 99/99、e203 pytest 2/2 通过。没有
native 模块时，MemoryBackend/asyncio 测试仍可运行，native 测试会 skip。

性能脚本位于 `benchmarks/benchmark_runtime.py`，覆盖 Raw Step、逐次
StepHalf、RunUntil 无 watcher/Value/Expr/FSM、pytrigger 和逐周期 Future 恢复；
`benchmark_coverage.py` 测量 point/cross 每事务成本，e203 另有真实 DUT A/B benchmark。

## 通用事务机制（2026-09-28）

Scoreboard 已提供显式 clock/response timeout、带预算的 drain/finish、额外输出检查和
独占 Monitor 生命周期。Monitor 跨提交批次持续运行；取消只允许撤销尚未接受的请求。
已接受请求在异常关闭时失败。Scoreboard 支持 async context manager，保留独立清理错误，
活跃表不再保存全部已完成请求。默认比较复用一次结构差异，关联逻辑在私有模块内。

SyncDriver/AsyncDriver 共享私有输入并发控制、资源锁和事件校验；串行 worker 仍复用
协程。AsyncDriver.send 返回接受即完成的输入句柄，submit 的响应由调用方完成。
两者现均调用即提交，具体变化见下方 2026-09-29 的提交接口记录。
本次没有新增 scope、广播、回放或回归管理框架，也没有改变具体协议组件的时序。

本轮完整回归为 180 项通过，包含 7 项新增 native 事务测试；严格文档构建通过。
Cache/e203 的完整 RTL campaign 未在本轮重跑。

用户契约见 [Scoreboard 指南](../docs/guides/scoreboard.md)，定向验证与限制见
[事务生命周期回归](../tests/integration/test_transaction_lifecycle_native.py)。

## 易用性更新（2026-09-28）

- 快速开始提供完整的协议无关 pytest 测试和项目 DUT fixture 接入配方；新增 native
  binding 自检与 capture/handler 示例。CI 执行入门、自检、订阅和事务四个入口。
- 文档明确超时 Trigger 返回事件、AnyOf 的已完成来源仲裁和 TaskComplete 的取消隔离；
  通过任务选择表区分 send/submit、drain/finish/aclose，并区分常用 API 与扩展协议。
- ScoreboardTimeoutError 提供 operation、周期预算、经过时间、起始/截止/当前 tick、
  CheckContext 和失败时统计；固定延迟显示实际窗口，finish 排空超时正确标为 finish。
  调度、deadline 判定、协议时序和 ownership 契约保持原样。

更新后强制 native 回归 181 项通过，独立入门 pytest 示例 1 项通过；native binding、
订阅与事务示例通过，严格文档构建通过。没有新增公共调度、等待 helper 或订阅入口。
详情见[功能与回归索引](../docs/reference/feature-map.md)。

## 流水输入示例验证（2026-09-28）

以下保留初版示例的验证结果。当前用户示例已改用 AsyncDriver.send 句柄提交，
具体接口与验证见下方“Driver 提交入口”一节。

新增 `examples/transactions/pipeline.py` 与[流水输入示例指南](../docs/guides/pipeline-examples.md)，
覆盖连续提交、串行/重叠输入阶段、并发 send、FIFO/固定延迟/key 响应关联，以及漏响应、
迟到、错误关联和容量不足。示例直接继承当前 SyncDriver/AsyncDriver，不依赖 SingleCycle
模板；本次没有实施公共 API 改名或调整框架调度。

初版多阶段示例在释放第一阶段锁后显式让出协程。2026-09-29 已用下述框架交接机制
替代，并删除示例中的 sleep(0)。测试检查每个 rising phase 的实际信号样本，区分真实
阶段重叠与仅有 Python 执行日志交错。

MemoryBackend/native XClock 共 22 项新增回归通过；完整强制 native 回归更新为
**203 项通过**。两种 backend 的六个成功 CLI 场景及四个预期失败 CLI 场景均运行验证，
严格文档构建通过。没有构建 RTL 或据此推导真实 DUT 吞吐。

## Driver 阶段交接（2026-09-29，已由观察器替代）

以下保留初次修复的历史结果；私有 permit/token 现已移除，当前机制见下一节。

通用 Driver 的 resource_lock 和 max_active 使用私有 FIFO permit。授予等待者时向
Reactor 登记交接，等待者从 acquire 恢复时确认；Pump 在 RunUntil 和 DriveStable
采样前处理这些交接。连续级联同样生效，无需用户手动 sleep；无争用时不新增 task/yield。
取消与失败回收名额，Execution 退出解除屏障。普通 asyncio 同步对象及外部任务仍遵循
原契约，不把整个 event loop 当作硬件调度器。

同时修复 paused() 进入时被取消可能遗漏 pause-depth 回收的路径。阶段定义、idle、
响应关联与具体协议时序未调整。设计、回归和性能证据见
[Driver 交接回归](../tests/integration/test_driver_handoffs.py)。

新增交接测试 18 项通过；完整强制 native 回归为 **221 项通过**，严格文档构建及两种
backend 的流水 CLI 示例通过。逐周期微基准前后约 559 ms，未观察到明显回退；详细
参数、原始样本与无 RTL 测量边界保存在修复记录中。

## asyncio 调度观察器（2026-09-29）

Execution 按目标 context 观察原生 call_soon 回调，收敛后才推进 RunUntil 或进行
DriveStable 采样。asyncio 保持唯一调度器；原生 Task/Handle/Lock/Queue/Event 和
TaskGroup 保留原有语义。多个 Execution 共用观察器，各自判断收敛，最后退出者恢复入口。

external_task 创建域外原生任务，清除隐式 Reactor 绑定，但保留用户 ContextVar。
用户创建的任务仍由用户清理。max_settle_rounds 为有界失败诊断，超限报出 tick、phase
和未完成回调，不跳过工作强行推进。Task factory、exception handler 和 callback
参数校验保留；进入失败、外部取消和第三方替换入口的恢复路径有正式回归。

新增观察器集成回归 46 项；完整强制 native 回归 **267 项通过**，严格文档构建、
两种 backend 的流水示例与入门示例均通过。验证与实测见
[观察器设计与验证入口](architecture/asyncio-observer.md)。

## Driver 提交入口（2026-09-29）

AsyncDriver.send 调用时提交输入并返回 XTransfer，接受时自动完成；submit 则等待
响应关联方完成。保留 await send 的用法，先提交多个 send 句柄即可让 Driver 管理
输入重叠，不增加 parallel 或 submit_input 接口。日常示例不再创建 TaskGroup。

Driver 基类只约定 Awaitable[XEvent]，SyncDriver 仍执行原来的 coroutine；具体协议
Driver 和 SingleCycle 时序不变。AsyncSingleCycleDriver 继承调用即提交的入口。
迁移时需注意 AsyncDriver.send 返回值不再是 coroutine，未 await 也会提交。

新增 memory/native 回归 44 项；完整强制 native 回归 **311 项通过**，严格文档构建、
两种 backend 的流水 CLI 示例及独立 Scoreboard 示例通过。见
[Driver 使用契约](../docs/guides/drivers.md)。资源锁仍保持现有实现，
其领域适配边界已整理为[讨论稿](architecture/driver-resources.md)，尚未新增公共资源类。

## 协议 Driver 范围收敛（2026-09-29）

当时的 Driver 层次在评审中进一步收敛：独立 ReadyValidDriver 已移除，
公开保留 Driver/SignalDriver、Sync/Async 模板及其 SingleCycle 子类，共六个。
后续新增的固定阶段 MultiCycle 模板见本文开头及当前 Driver 指南。
协议实现应派生自 Sync/Async；Cache 项目的 CacheDriver 已迁移到 SyncDriver。
311 项强制 native 回归及严格文档构建通过，详见
[协议驱动指南](../docs/guides/protocols.md)。

## 验证流程缺口修复（2026-09-29）

这些审计问题已在后续修复：Monitor 关闭或 capture/decoder 失败会唤醒 recv 等待者；
关闭清空未读缓存，同一实例不可重启。已观察错误不在退出时重复报告，无人接收的
错误由组件关闭或 context 退出报告。SamplingMonitor 提供显式 trigger + 同步 capture，
和 ReadyValidMonitor 共用私有生命周期实现，具体协议接受时刻保持原规则。

当时修复的 pytest 标签聚合后来按用户确认移除，原生 pytest 继续负责执行与退出码；
当前功能覆盖依据各次运行的实际 bin 命中合并，见下文“功能点与跨 case 覆盖”。

覆盖率和运行产物通过项目配方接入，Scoreboard 保持唯一 Monitor 消费者；包括
失败时保存、seed/nodeid 隔离、明确预算及反向清理。该轮没有新增公共 Agent、自动覆盖率
fixture、SimulationManager 或资源包装类。强制 native 全量 **379 项通过**，严格文档
构建通过；详见[验证流程与运行产物指南](../docs/guides/verification-flow.md)。

## Agent 实例与 reference model 接入（2026-09-29）

Agent 封装可选 Driver、具名 Monitor 和响应检查连接，环境构造实例后调用 connect(ref)，
通过 Execution(backend, agents=[agent]) 统一启动和清理。日常入口是 agent.send/submit、
recv、drain/finish，不再要求用户进入 Agent context 或拆出组件绑定 Scoreboard。
旧的 Agent.bind/__aenter__/aclose 草案已移除。

SyncDriver 的 send 仍在调用方执行；Agent 在接受后交付内部检查器继续跟踪响应，不将它
改造成可后台 submit 的 Driver。AsyncDriver 仍复用原 worker；checked send 返回接受
视图，submit 返回响应 XTransfer。SingleCycle 时序、idle 策略及协议判断没有改动。

模型独立于 Agent 生命周期。内部接受适配层在实际接受后调用同步 ref.accept(request)，
推进模型状态并保存预期；显式 expected 只覆盖本笔比较值，不跳过模型更新。默认预测
仍为单请求、单响应，支持 FIFO/key/固定延迟。独立 Scoreboard(expected=...) 的条件式
预测回调语义保持不变。

finish 封口、排空并检查指定观察窗口，但不销毁 Agent 或 Monitor。Execution 按注册
顺序启动 Agent、反向关闭；启动失败回收已启动部分，异常和外部取消仍继续清理、保留
原始异常。内部 Scoreboard 独占响应 Monitor，Agent.recv 不允许第二条消费路径。
不引入新调度器、parallel、公开 TaskGroup 或协议推断。

共享模型可以连接多个 Agent，但跨接口同 tick 顺序仍由项目定义。纯被动模型关联、
多输出和远程异步模型适配不属于当前自动响应链。
实现和验证见[Agent 与参考模型指南](../docs/guides/agents-and-reference-models.md)。
强制 native 全量 **433 项通过**，严格文档构建、两种 backend 的参考模型示例及覆盖率
示例测试均通过。

## 按功能重组用户文档与回归（2026-09-29）

用户指南按仿真/事件、数据、Driver、Monitor、Agent/模型、Scoreboard、覆盖率/pytest
和运行场景组织。新增 Agent fixture quick start，默认 pytest 同时执行框架回归和入门
示例；功能索引连接指南、可运行代码与实际行为测试，顶层 125 个导出全部有 API 入口。

私有清理错误处理合并到 _cleanup_errors，Monitor 共用关闭/交付契约和双后端 fixture，保留
特有协议采样及各类 Driver 行为。新增窗口/Hold/FSM 优先级、callable idle、coverage
门控/权重/reset 的针对性回归。实际 genhtml 验证发现单源码文件的自动 prefix 会导致
失败，已删除该路径猜测并用真实工具覆盖，CI 安装 lcov。

本轮强制 native 全量 **457 项通过**；严格文档构建、从文档提取的两个 quick start、
12 个 CLI/报告场景及渲染页面链接检查均通过。范围与证据见
[功能与回归索引](../docs/reference/feature-map.md)。

## 从零开始的递进教程（2026-09-29）

前一轮的功能指南保留为查询手册；入门部分按零基础学习顺序重新编写。
同一个 8 位累加器贯穿 assert、时钟输入、边界用例、Driver、Monitor、Agent、模型、
fixture、流水、多批次、错误诊断和覆盖率。完整主线代码随仓库提供，各页讲解本节新增
部分、运行命令、预期输出与改错练习，按需解释 Python 语法。乱序接口和 native XClock
放在选读部分。

教程文件随后由 20 个 Python 文件收拢为 6 个。主线只看 test_accumulator.py 与
toy_dut.py，Driver/Monitor/Agent/模型定义按教学顺序紧挨使用处，各章按测试函数选择运行。
入门目录保留 16 项正常用例；故障练习、native 选读显式运行。

首版曾完成强制 native 全量 488 项检查。文件收拢时删除六项源码字符串改写测试及六项
重复后端矩阵，保留文档片段一致性和四种故障退出检查；native 贯通例子由 CI 显式运行。
本次按改动范围验证：教程及相关检查 21 项通过，native 选读 1 项通过，严格文档构建通过。
说明与证据见[入门代码与检查入口](../examples/getting_started/README.md)。

## 真实 DUT 示例更新（2026-09-29）

e203 与 Cache 均用当前 native binding 重新生成 DUT，接入现有 Scoreboard 与覆盖率。
e203 新增第二命令背压、+4 顺序、全部错误组合、区域跨界和地址回绕；Cache 新增全部
refill 起始 word、各字节 mask、零 mask、clean replacement 和整行 dirty victim 核对。
Cache 三组各 300 次随机操作通过。早期同步 overlap 探针误报已在 2026-10-01 修正，
详见上方采样时点说明；当前独立同步探针及异步重叠回归通过。

e203 主 campaign 生成 functional 和 LCOV 报告，脚本返回 pytest 原生结果。两套环境均在比较失败时
保留产物并释放组件任务。项目驱动时序和通用框架协议边界保持原有规则，没有新建调度或
回归管理设施。后续经用户明确授权，在 Cache 项目内增加 CoherenceAgent，封装
SyncDriver 和具名 Monitor，项目层组装多拍响应并做 Scoreboard 比较；通用框架仍保持
协议无关。覆盖 miss/clean/dirty、全部起始 word、头/首/中/末拍背压；flush 尚未覆盖。

教程仍为 6 个 Python 文件，主线只读两个；真实 DUT 环境复用原文件，不为每个 feature
拆测试模块。相关入门、流水、Agent、native 生命周期和产物检查共 153 项通过；实际场景
与测量记录见 [Cache 示例说明](../examples/integration/cache/README.md) 和 [e203 示例说明](../examples/integration/e203/README.md)。
独立 native 教程 1 项通过，mkdocs 严格构建通过。本轮按影响范围验证，没有重复运行
全仓库测试；既有全量记录仍按原日期保留。

Coherence 授权增量单独运行项目回归：8 项通过，三组各
300 次 Cache 随机操作通过。测试集中在一个文件，包含错误头/数据/末拍、漏拍、额外拍、
旧响应及外部取消，核对失败产物和 native 清理；完整命令见 Cache 示例 README。

e203 后续增量在原文件内补足 23 项 native 回归；默认 seed
跑完整用例，另外三个 seed 只重复主 campaign。定向输入扩展到 34 笔，增加 holding/nohold
连续切换和独立拆分响应延迟。故障回归先复现再修复了响应受阻时短暂撤回 valid 被漏检的问题，
并补充接受前响应诊断。失败产物保留未完成请求和命令轨迹；三个取消阶段检查无任务、watcher
或 backend ownership 残留，宿主外部任务保留。本轮未改通用框架，未新增测试文件，
验证范围限于 IFU-to-ICB 模块；详见上述 e203 记录。

## 功能点与跨 case 覆盖（2026-09-29）

功能点由设计要求定义，覆盖来自各 case 实际观察到的场景。已移除人工测试标签聚合：
FeatureTracker、pytest feature marker/plugin、对应公开 API、CLI 参数和 HTML 完成率
页面不再提供。pytest 负责执行和原生测试结果，CoverageDatabase 负责采样计数与合并；
没有用另一套计划管理器替代这层功能。

运行产物示例改为两个 case，各自观察 tag 1/2 和 2/3，单次覆盖均为 2/3，合并为 3/3。
稳定的覆盖实例名用于跨运行合并，nodeid/seed 留在运行元数据中。兼容 schema 的同名
bin 累加计数；Cross 合并实际命中组合，不从不同 case 的单维命中构造新组合。
已有失败、取消和导出异常的产物保存与清理契约继续保留。

e203/Cache 的 verification_plan.json 保留 `scope` 和 `requirements`，记录源码基线、
激活条件、行为要求、观察对象和必需场景；测试不再读取计划生成标签，也不报告计划完成率。
两份清单中的 49/77 个内部义务已落实为内部场景采样和属性检查。真实激活与比较错误
分别保存；场景命中不能替代正确性。现有断言、故障回归和端到端模型继续保留；新增激励依据
[e203 验证计划](../examples/integration/e203/verification_plan.json)推进。

用户指南见[功能点与跨用例覆盖](../docs/guides/functional-points.md)，
Cache 义务见[验证计划](../examples/integration/cache/verification_plan.json)。

## 有状态覆盖与 native 采集（2026-09-30）

已实现 `Bin.transition`、Sequence 的 `Next`、`CoverGroup.bind()` 和
`Execution(coverage=[group])`。静态点、同样本 cross 和相邻转移可在 xcomm 持续累计；
已有编译 Expr/Sequence/FSM 可作为采样源。Python 事务采样继续可用，整组回退会报告原因。

未新增公开 Binding 类。默认按 Execution 清统计；显式 `accumulate=True` 保留完成计数，
两种方式都重建匹配历史。report/sync 对 native 累计快照计算差量，reset 同时更新 epoch。
退出时先停止推进，再同步并释放覆盖注册，最后清后端程序缓存。部分启动失败、用例异常、
取消与快照错误均走同一清理路径。绑定层检查采样契约，禁止不兼容的累计和合并。

xcomm 扩展 `AttachCoverage/CoverageSnapshot/ResetCoverage`，共享现有 handle 和 phase
求值入口；普通 count 命中不产生 XBackendHit。SWIG 为 Trigger 引擎转换 C++ 异常，避免
非法命中直接终止宿主进程。coverage ABI v2 将 point 来源改为直接 XData，复用已有宽
比较并增加完整位宽的 mask 比较；值、范围、转移、Iff 和非法快照均不再限 64 位。
任意高位的 X/Z 都按未知样本处理；计数器仍为独立的 uint64 溢出检查通道。

当前 coverage ABI 为 v3：复杂 Sequence/FSM 绑定可设置 `overlap=True, max_active=N`，
默认仍为单实例。每个活跃匹配仅保存步骤/状态等进度，共享不可变程序，C++ 线性推进和
压缩活跃表；同拍先完成旧匹配再启动新匹配。容量耗尽明确失败并标记采集不完整。
不创建额外任务，不改变普通 await/subscription 的触发器时序。

`diagnostics="summary"` 可选收集启动、完成、失配、过期、中止、清历史、并发峰值和
退出未完成数，默认关闭。`inspect()` 在运行内按需读取当前状态，不保存轨迹。
汇总以折叠表格进入报告，不参与覆盖分母；累计/合并只处理统计，不继承活跃匹配。
退出先快照未完成数再释放，异常与取消保留原始错误。混合诊断开关的运行标明各自数量。

这完成了[实施计划](delivery/stateful-coverage-plan-2026-09.md)的基础采集路径；capture、
key 并发上下文及可配置 pause 仍未实现；真实 e203/Cache 内部观测现已落实。现有项目
54/43 个事务 bins 的范围不因此扩大。使用入口见[覆盖指南](../docs/guides/coverage.md)。
实现边界、生命周期回归及微基准见[功能覆盖设计](features/functional-coverage.md)。

## Coverage 与真实 RTL 属性（2026-10-01）

覆盖计算排除被 ignore/illegal 完全遮蔽的普通 bin；cross 使用无歧义 tuple ID；
默认验收结合子项目标、非法命中和采集完整性。报告保留运行及快照身份，合并拒绝重复
或累计来源重叠，按 bin 保存命中来源。当前仅支持报告格式版本 2。

ready/valid Driver 在全部输入驱动结束后的 DriveStable 判断 ready，随后等待实际
上升沿接受，避免同拍仲裁变化造成假接受。native 回归核对该时序及 payload/tick。

e203 49 项内部要求共 133 个必需场景 bins，均满足当前基线关闭条件。Cache 81 项共
412 个 bins，80 项关闭，CPU read-burst 数据及提前退休错误保持未关闭。模块与完整 Cache 测试
共享 native runtime；检查器错误快照不贡献 DUT 覆盖。完整框架回归 560 passed，
e203 31 passed，Cache 47 passed，其中四个 Cache PASS 表示复现预期 RTL 错误。

2026-10-02 按 RTL 行覆盖新增 Stage2 flush、MMIO 请求背压及完整 Cache 读 burst
场景。同构建的 Cache 执行行覆盖由 331/350 提高到 336/350，执行覆盖点由 267/281
提高到 272/281。2026-10-03 补齐模块/顶层五类指定断言、LFSR 全零恢复、非法控制
状态复位及异常 flush 标志清理。断言子进程保存计数后继续原 fatal，核对确切诊断、
fatal 位置和 SIGABRT。执行行及覆盖点达到 350/350、281/281，未修改 RTL 或排除项。
故障注入有独立证据；未提供规格的非法命令和自动恢复不据实现自行定义新契约。
统计使用 Verilator 自带 LCOV 转换器，执行计数、toggle 和生成封装
分别保留；模块导出不混入顶层覆盖结论。逐点范围及复现命令见 Cache 示例 README。

具体构建与运行入口见上述项目 README。分析报告、性能结果、覆盖数据库及日志是本地
生成产物，不纳入 Git；正式契约由设计文档、验证计划与检查代码保留。

## 尚未完成

### P0：进入生产使用前应优先解决

1. **native program 生命周期**：相同 IR 有 cache，但大量唯一 Expr/FSM program
   的 ExprEngine node 已在 Execution 退出时统一清理；单次长 Execution 内仍缺少
   refcount/arena 回收，需单独测量唯一 IR 的增长。
2. **完整性能矩阵**：e203 memory-direct 已形成包含 DUT eval 和 Verilator coverage 的
   吞吐基线；waveform 开启后的代价、长期运行内存曲线和 HTTP latency p50/p99 尚缺。
3. **错误模型**：StopReason 类型已定义，但 callback/backend exception 主要通过
   Python exception 传播；USER_PAUSE/SIMULATION_CLOSE 等并非都由 RunResult 发出。

已完成的原 P0 位宽项：XData 本身的任意位宽读写已贯通到 native `Value`、
`ValueChange` 和直接 `@xtrigger` 比较；`PackedArray/split_packed` 可将单根 Verilog
packed bus 显式拆成等宽 XData lane，`PackedLayout/PackedView` 进一步支持多维数组、
数组/Struct 任意嵌套、每层位序、stride、显式 offset 和 padding。布局只在构造时编译，
leaf 都直接引用根 XData。宽 signed/cast 与宽算术仍是独立语言设计，不属于此次
无符号位宽修复。

### P1：按真实用例选择的扩展

1. 多时钟、分频 clock 的共同 global tick 和 deterministic ordering；
2. 跨 domain AnyOf/Sequence/FSM 和 domain/backend pause；
3. 写缓冲、同 phase 多 driver 冲突诊断；
4. 显式 signed/cast/width IR，以及确有需求时的宽算术；
5. overlapping FSM instance；当前只支持非重叠。

### P2：测量驱动的优化与生态验证

1. SWIG hit 的只读 zero-copy buffer/view；
2. GIL release，以及 profiling 证明需要时的 simulator owner thread；
3. adaptive batch/quantum；
4. pytest-asyncio、aiohttp、FastAPI 的安装后 CI 兼容矩阵与 latency p50/p99；
5. trace、结构化错误上下文、内存/长期 subscription 压测；
6. Bundle/ReadyValid 的 native batched snapshot。框架保留通用 Driver/Monitor 与显式
   Bundle binding 和可选具体通道视图（不含通用 Interface 基类）；协议类型组织、
   项目专用模型与 pytest 工程脚手架由上层代码或 agent 生成；通用 Scoreboard、
   事务生命周期和覆盖报告已由框架提供。Cache 的八个 ready-valid 子树均已
   绑定，CPU/memory/MMIO 功能流量已通过统一抽象；coherence 已有项目 Agent 的
   probe/release 验证，尚未覆盖 CPU/coherence 并发仲裁。

## 推荐下一步

最短、风险最低的推进顺序是：

```text
program arena/refcount + 内存压力测试
  -> 补齐 waveform/HTTP/长期运行性能矩阵
  -> 按 profiling 决定 zero-copy hit / GIL / owner-thread
  -> pytest/HTTP 兼容矩阵
  -> 真实用例需要时再扩展多时钟 domain 模型
  -> native batched Bundle snapshot
```

因此，当前版本适合继续做单 XClock 调度域 DUT 的 API 验证、语义测试和性能迭代。
它不以复刻 cocotb event-region scheduler 为目标；多时钟是否扩展由真实用例决定。
