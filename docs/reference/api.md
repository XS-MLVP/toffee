# 公共 API 索引

所有常用对象都从 `xreactor` 顶层导出。

第一次使用请先阅读[从零开始的教程](../getting-started/index.md)。按需求查找：

| 需求 | 常用入口 |
| --- | --- |
| 推进仿真、等待条件 | Execution、ClockCycles、Value、AnyOf |
| 驱动、采样结构化数据 | Bundle、SignalDriver、Transfer |
| 跟踪事务、比较和结束检查 | XTransfer、Scoreboard；见[接口选择表](../guides/scoreboard.md) |
| 功能覆盖与报告 | CoverGroupDef、CoverGroup、CoverageDatabase |

Backend 扩展协议与底层推进类型放在本页最后；编写普通测试无需实现这些协议。

## 执行与 Backend

| API | 用途 |
| --- | --- |
| `Execution` | 一个测试的异步运行作用域 |
| `Execution.external_task(coro, *, name=None)` | 创建调用方负责清理的域外原生 Task；不携带隐式 Reactor 绑定 |
| `Execution.paused()` | 显式暂停仿真时间，宿主 asyncio 继续运行 |
| `SimulationNotSettledError` | 当前阶段就绪工作超过 max_settle_rounds，时钟不越过屏障 |
| `XCommClockBackend` | XClock/C++ trigger engine adapter |
| `MemoryBackend` | 纯 Python 语义测试 Backend |

## Trigger 与 Event

| API | 用途 |
| --- | --- |
| `RisingEdge`, `FallingEdge`, `DriveStable` | 稳定阶段触发条件 |
| `ClockCycles` | 上升沿周期计数 |
| `Value`, `ValueChange` | 指定 phase 的值条件和变化 |
| `SimTimeout`, `WallTimeout` | 到期返回 TIMEOUT 事件，调用方决定是否抛异常 |
| `AnyOf`, `AllOf` | 组合等待 |
| `AsyncioEventTrigger`, `QueueTrigger`, `TaskComplete` | 把 asyncio 事件接入等待 |
| `XEvent`, `EdgeEvent`, `ConditionEvent`, `FsmEvent` | await 返回结果 |
| `XPhase`, `XEventKind`, `LogicValue` | phase、事件类型和四态值 |

## 编译和订阅

| API | 用途 |
| --- | --- |
| `@xtrigger` | 编译到原生 IR 的条件/Sequence/FSM |
| `@pytrigger` | 明确的 Python 条件函数 fallback |
| `Next(expr)` | Sequence 中严格的相邻 sample 条件，失配重新等待起点 |
| `Bin.transition(*values, overlap=True)` | 相邻采样值的转移 bin，复用 Sequence 匹配语义 |
| `CoverGroup.bind(trigger=..., fields=..., strategy="auto", accumulate=False, overlap=False, max_active=None, diagnostics="off")` | 配置被动采集；复杂模式显式启用有界并发，汇总诊断可选；由 `Execution(coverage=[group])` 管理 |
| `CoverGroup.sync()` / `clear_history()` | 同步原生统计；仅清除未完成匹配 |
| `CoverGroup.inspect()` | Execution 内按需读取当前步骤/状态与等待进度，不保存轨迹，也不要求开启诊断 |
| `Sequence`, `Wait`, `Within`, `Hold` | 默认非重叠观察模式；coverage 绑定可启用并发；未命中不会自动让测试失败 |
| `FSM`, `State` | 显式分支状态机 |
| `@on` | 持久订阅声明 |
| `Execution.subscribe` | 启动绑定后的 subscription |

## 数据与协议

| API | 用途 |
| --- | --- |
| `as_xdata` | 取得/校验原生 XData |
| `Bundle`, `BundleValue` | 结构化 实时信号视图 和不可变 snapshot |
| `PackedArray`, `split_packed` | 等宽 packed bus 拆分 |
| `PackedLayout`, `PackedView` | 嵌套、自定义 packed 布局 |
| `ReadyValid`, `Decoupled`, `Role` | 可选的 ready/valid 通道视图 |
| `Driver`, `SignalDriver`, `Monitor` | 组件接口与不依赖 Bundle 的信号所有权基类 |
| `Agent` | 封装 Driver/Monitor 的接口实例；connect(ref)、send/submit/recv、drain/finish；由 Execution(agents=[...]) 管理 |
| `ReadyValidMonitor` | 被动观察 ready/valid 握手 |
| `SamplingMonitor` | 显式 trigger 与同步 capture；保留触发事件的通用采样模板 |
| `MonitorClosedError`, `MonitorOverflowError` | 内置 Monitor 的终止与 lossless 容量错误 |
| `SyncDriver`, `SyncSingleCycleDriver`, `SyncMultiCycleDriver` | await send 等待输入接受；通用基类、单周期和固定阶段多周期模板 |
| `AsyncDriver`, `AsyncSingleCycleDriver`, `AsyncMultiCycleDriver` | 后台提交基类及单周期、固定阶段多周期模板；send/submit 调用时均立即提交 |
| `DriveStage` | 多周期模板的静态阶段描述，含信号、idle、编码和占用周期 |
| `AsyncDriver.send(transaction) -> XTransfer[RequestT, XEvent]` | 接受时自动完成；await 得到接受事件，不要求响应收集器 |
| `AsyncDriver.submit(request) -> XTransfer[RequestT, ResponseT]` | 返回待响应的事务句柄，由响应关联方完成 |
| `Transfer` | Monitor 交付的事务和接受事件 |

## 功能覆盖率

| API | 用途 |
| --- | --- |
| `Bin`, `BinSpec`, `BinKind` | bin 定义 |
| `CoverPointDef`, `CrossDef`, `CoverGroupDef` | 可序列化 coverage schema |
| `CoverGroup` | 一个运行实例及 counters |
| `CoverageDatabase` | 多实例保存、读取和合并 |
| `Iff` | group/point/cross gate |
| `IllegalPolicy`, `OverlapPolicy` | illegal 和 overlap 策略 |
| `generate_unified_coverage_site` | 多页 functional + LCOV 报告 |
| `generate_unified_coverage_report` | 单文件统一报告 |

以上名称从 `xreactor` 导入。用类组织覆盖模型时，从 `xreactor.declarative` 导入：

| API | 用途 |
| --- | --- |
| `@coverpoint`, `CoverPoint`, `Bin` | 把分桶规则写成可复用的类 |
| `@covergroup`, `CoverGroup`, `Cross` | 定义样本类型、覆盖点和交叉覆盖 |
| `Fields`, `wire` | 指定字段路径，并绑定已有信号 |
| `TemporalCoverPoint`, `SignalCoverGroup` | 直接对跨周期事件计数 |

参见[类声明](../guides/coverage-classes.md)、[时序覆盖](../guides/coverage-patterns.md)。
声明层的 `CoverGroup` 与顶层同名类来自不同模块；它的 `runtime` 属性用于接入既有运行和报告机制。

## Transaction 与 Scoreboard

| API | 用途 |
| --- | --- |
| `XTransfer`, `TransferState`, `MISSING` | request 从提交、接受到完成/失败的 awaitable 句柄；取消 waiter 不撤销事务 |
| `Scoreboard` | 直接检查或绑定 driver/monitor 后异步关联检查 |
| `CheckContext`, `ScoreboardStatus`, `Difference` | 比较上下文、诊断状态与字段差异 |
| `ScoreboardMismatch` | expected/actual 不一致，继承 `AssertionError` |
| `ScoreboardAssociationError` | 关联冲突、未匹配输出或观察容量溢出 |
| `ScoreboardIncompleteError` | 关闭时仍有未完成事务 |
| `ScoreboardTimeoutError` | 周期预算失败，含 operation、budget_cycles、elapsed_cycles、tick 与 context 等结构化属性 |
| `DriverIncompleteError` | Driver 关闭时仍有已接受且未完成的请求 |
| `MissingExpectedError` | 异步事务没有显式期望或 expected source |
| `structural_differences` | dataclass/mapping/sequence/BundleValue 的递归差异 |

完整示例见 [Scoreboard 与流水线事务](../guides/scoreboard.md)。

## 实现 Backend 或自定义组件时使用

| API | 用途 |
| --- | --- |
| `SimulationBackend` | Backend 协议 |
| `BackendCapabilities` | Backend phase/capability 声明 |
| `RunLimit`, `RunResult`, `StopReason` | Backend 批量推进接口 |
| `AcceptedDriver`, `ObservedMonitor` | 异步 Scoreboard 使用的结构化组件 Protocol |

## 覆盖率与运行报告

| API | 用途 |
| --- | --- |
| `CoverageSample`, `IllegalHit`, `IllegalBinError` | 本次命中明细、非法记录与失败 |
| `CoverageSchemaError`, `CoverageMergeError` | 无效定义或不兼容的合并 |
| `CoverGroupDef.instantiate(..., run_id=None, run_metadata=None, contract=None)` | 运行身份、上下文与手动事务采样规则 |
| `CoverGroup.goal_met`, `CoverGroup.covered` | 组百分比达标与包含子项目标、非法命中、完整性的覆盖验收 |
| `CoverGroup.assert_coverage(minimum=None, per_item=True)` | 默认同时检查正权重子项目标；可显式只检查组阈值 |
| `CoverGroup.from_report(report)`, `CoverageDatabase.read_json(path)` | 读取当前格式的报告与数据库 |
| `parse_lcov` | 解析 simulator 行覆盖率并合并同文件记录 |
| `build_unified_coverage_model`, `render_unified_coverage_html` | 显式组合数据并渲染自包含报告 |

覆盖率采样参数、cross/门控、保存与合并见 [Coverage](../guides/coverage.md)。

## 声明类型和底层句柄

以下类型支持组件开发或诊断，普通用例优先使用前面的特性入口。

| API | 用途 |
| --- | --- |
| `Field` | Bundle.bind/view_as 的来源和位宽声明；见[数据](../guides/data.md) |
| `drive_ready_valid` | 项目协议 Driver 可选的握手驱动辅助函数；见[协议视图](../guides/protocols.md) |
| `XTrigger`, `PhaseTrigger`, `ConditionMode` | Trigger 契约、采样阶段和命中模式 |
| `PythonPredicateTrigger`, `CompiledTrigger` | 显式 Python 条件函数 与 IR 条件实例 |
| `PhaseEvent` | DriveStable 等阶段事件 |
| `XExpr`, `SequenceSpec`, `FsmSpec` | 编译表达式及时序规格；优先用装饰器与构造函数声明 |
| `XSubscriptionSpec`, `BoundSubscriptionSpec` | @on 声明与绑定结果；见[订阅](../guides/subscriptions.md) |
| `Subscription`, `SubscriptionOverflowError` | 运行订阅句柄和 lossless 队列溢出 |
| `XReactor`, `Registration` | 组件层的事件注册、分发和取消，由 Execution 管理 |
| `BackendHandle`, `BackendHit` | 后端 arm/disarm 身份及命中记录 |
| `ScoreboardMode`, `ScoreboardError` | 直接/异步检查模式和 DUT 检查异常基类 |

顶层导出以 `src/xreactor/__init__.py` 的 __all__ 为准。常见路径、示例与回归的对应关系见
[功能索引](feature-map.md)，具体支持边界见[行为与限制](semantics-and-limitations.md)。
