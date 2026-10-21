# Coverage v2：每个 bin 直接使用 xtrigger

这是 coverage v2 草稿。当前适配 picker/xcomm 的 `xreactor-coverage-v2` 分支；原生 ABI 为 3，coverage 描述协议为 5。精确提交与本地构建方式见下文。

这轮接通了完整执行路径：**一个 bin 引用一个现有 xtrigger 定义，C++ 持续推进并计数**。现有 Bundle、表达式 IR、Sequence/FSM 推进函数、coverage 计数存储、快照和报告继续复用。

## 定义与使用

可运行的完整示例见 [trigger_bins.py](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/examples/coverage/trigger_bins.py)，引脚组和 DUT 替身直接复用 [declarative_protocol.py](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/examples/coverage/declarative_protocol.py)。

```python
@xtrigger(sample=RisingEdge("clock"))
def roundtrip(pins: ProtocolBundle, *, tag: int = 1, maximum: int = 4):
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == tag)),
        Within(1, maximum,
               signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
               & (signal_expr(pins.response.tag) == tag)),
    )


class Completions(PatternCoverPoint[ProtocolBundle]):
    tag_one = roundtrip
    tag_one_fast = Bin.pattern(roundtrip.with_args(maximum=2))


@covergroup(schema_id="protocol.completions", contract="protocol.completions/v1")
class ProtocolCoverage(SignalCoverGroup[ProtocolBundle]):
    completions = Completions()


coverage = ProtocolCoverage(instance="dut.protocol")
coverage.bind(pins=pins, sample=RisingEdge(pins.clock), strategy="native")
async with Execution(backend, coverage=[coverage.runtime]):
    await ClockCycles(pins.clock, 12)

count = coverage.completions.count(Completions.tag_one)
```

`ProtocolBundle` 是项目的普通 Bundle 子类；叶子仍是原始 XData。函数参数、字段和类属性可用于补全、跳转和重命名。报告名称取自类属性，查询直接使用声明对象。`clock`、schema/instance 标识和 FSM 状态名继续采用现有接口的字符串形式。

`PatternCoverPoint` 和 `SignalCoverGroup` 是现有声明层的子类：前者组织过程完成事件，后者绑定 live 根对象。它们仍使用一个 CoreCoverGroup 存储计数。快照与已完成事务继续使用既有 `CoverGroup[Snapshot]`，例如 `TransactionCoverage.sample(completed)`；两种输入的职责可从类定义看出。

## bin 与 trigger 如何复用

`@xtrigger` 现在返回不可变的 `TriggerDefinition` 元数据对象。它保存原函数、sample 和 mode，声明函数仍构造既有 XExpr/SequenceSpec/FsmSpec。

- `roundtrip(pins, maximum=3)` 返回现有 `CompiledTrigger`，可以正常 await。
- `roundtrip.with_args(maximum=3)` 检查原函数的参数签名，冻结参数，返回同一种未绑定定义对象。
- `fixed.bind(pins, sample=...)` 绑定冻结后的定义；显式阶段必须与定义声明的阶段一致。
- 直接放进 point 的定义使用默认参数；存在必需参数时要先调用 `with_args`。
- 参数冻结目前接受 bool、int、str、None 及这些值组成的 tuple，拒绝可变容器。
- `Bin.pattern(...)` 增加阈值、normal/ignore/illegal、FSM 终态选择及有界重叠策略。

`PatternBin[P]` 只是 `TriggerDefinition[P, ...] | BinRule[P]` 的类型别名，方便继承时替换配置；没有独立 matcher/trigger 层。相同定义可用于不同 points，运行历史独立。同一个 point 下重复使用同一对象会导致查询歧义，要求分别创建 `Bin.pattern(...)` 配置。

完整示例同时包含逐拍握手表达式、两个不同窗口的 Sequence、Wait/Within/Hold 组合，以及选择 `OK`/`BAD_DATA` 的 FSM。多终态 FSM 必须指定 `terminals=(...)` 或 `all_terminals=True`；到达未选择的终态也结束该次匹配。ENTER/EACH_SAMPLE/CHANGE 直接沿用普通 trigger 的事件语义。

## 同一次观察，多个结果 bins

同一个 point 中，多个 bins 可以引用相同的完整程序，分别选择它的不同终态：

```python
class Completions(PatternCoverPoint[ProtocolBundle]):
    correct_data = Bin.pattern(checked_tag_one, terminals=("OK",))
    bad_data = Bin.illegal(Bin.pattern(checked_tag_one, terminals=("BAD_DATA",)))
```

这两个 bins 共用一次 FSM 推进，C++ 根据结束结果分别增加计数。到达未选择的终态也会结束这次匹配；终态选择不改变过程本身。normal/illegal 属性、覆盖阈值和计数仍各自独立。

编译器按照现有 IR 的规范化内容识别相同过程，忽略终态选择、kind 和阈值。共享限定在同一个 point、同一绑定和采样生命周期内，mode、overlap、max_active 必须一致。两个不同窗口的 Sequence 分别执行；不同 points 的运行历史保持独立。

重叠请求仍各自有匹配状态。一拍内可以有 FAST 一次、SLOW 两次；引擎保留各终态的完成数量，分别分发给选择对应结果的 bins。Expr 和 Sequence 也可以共享同一观察结果，保留原事件模式和步骤语义。

声明写法沿用现有 Bin.pattern，不增加用户必须定义的中间层级。CompiledGroup.explain() 会显示例如 `execution 0 (checked_tag_one): bad_data, correct_data` 的关系。目标引擎的 CoverageState 使用 trigger 模块的 PatternMatcher，底层继续复用 AdvanceFsm/AdvanceSequence；原生 group 仍只占一份采样注册。

`CoverageExecutionCount(handle)` 是检查独立 pattern 执行数量的原生查询，排除 group 采样源和值 bins。协议示例的六个 bins 对应五份执行；两个终态 bins 共享一个 FSM。每个 bin 的诊断和 progress 继续显示该 bin 消费的观察过程，不能把这些视图相加当成实际资源数量。

该优化使用 coverage 描述协议 4，保持 schema version 3 和报告契约不变。配套原生库使用 ABI 3；两个版本号分别描述参数协议和 C++ 二进制兼容性。长 Execution 节点回收与两态 typed 表达式已在配套草稿接通；沿途 emit 事件及动态关联仍未实现。

## 编译与计数

```text
类声明与继承
  -> 现有 CompiledGroup：冻结参数、IR 序列化、schema、explain/diff
  -> 现有 backend lowering：绑定真实信号、编译表达式和步骤
  -> xcomm coverage 描述协议 4：共享的过程执行与各 bin 的结果选择
  -> trigger PatternMatcher / AdvanceSequence / AdvanceFsm / 表达式求值
  -> 原生累计计数与 CoverageSnapshot
  -> 同一个 CoreCoverGroup 的报告与 CoverageDatabase
```

pattern point 不需要虚构一个数值信号。每个 bin 的 matcher 保留完整程序，schema version 3 包含程序、参数、mode、终态和容量；更改窗口会改变 schema digest，报告不会误合并。普通值覆盖仍接受描述协议 3/4，trigger bins 的严格 native 路径要求协议 4；auto 路径遇到旧协议明示回退原因。

默认不重叠，`max_active=1`。重叠模式必须显式提供容量，例如 `Bin.pattern(roundtrip, overlap=True, max_active=8)`。同拍三次完成按三次计数。容量不足标记采集不完整并报错。reset、clear_history、abort 沿用现有执行生命周期；普通等待的取消不会清空 coverage 的历史。

ignore/illegal 在 pattern point 中修饰各自的过程事件，不取消同 point 下其他程序的完成计数。pattern point 的 samples 记录启用的采样次数，unmatched 表示该次没有任何程序完成；未知条件沿用现有 trigger 语义，普通值 point 的 unknown 统计不被借作过程诊断。

跨 temporal points 的 Cross 被明确拒绝；跨过程完成的关联上下文尚未定义。值 points、已完成事务记录的 Cross 继续使用既有实现。声明层 live 输入目前先支持 temporal points；typed gate 选择器仍面向快照字段，底层现有 Iff 的过程门控已经对照验证。

## 运行实验

在实验根目录运行：

```bash
python -m examples.coverage.trigger_bins --engine xcomm --strategy native
python -m examples.coverage.trigger_bins --engine memory --strategy python
```

示例发送 tag 1、2、3，响应顺序是 2、1、3。预期计数：

| point.bin | 次数 | 原因 |
| --- | ---: | --- |
| requests.handshake | 3 | 三笔请求握手，连续握手逐拍计数 |
| completions.tag_one | 1 | tag 1 三拍后返回，落入四拍窗口 |
| completions.tag_one_fast | 0 | 同一次响应不满足两拍窗口 |
| completions.tag_two_ready | 1 | tag 2 响应后，再连续保持 ready 两拍 |
| completions.correct_data | 1 | tag 1 响应数据为 10，FSM 到 OK |
| completions.bad_data | 0 | 没有错误数据 |

Python memory、xcomm Python 和 xcomm native 使用同一输入，对比计数。测试还覆盖同拍多个完成、FSM 未选终态、gate/abort/reset、普通 waiter 取消、容量失败、报告序列化，以及 native 不调用 Python sample、不因每次命中唤醒 Python。类型测试直接导入真实实现，检查参数名/类型、输入根类型和 bin 引用。

## picker/xcomm 分支适配

集成以 picker `4dd442c`、xcomm `e64d862` 的 `xreactor-coverage-v2` 分支为基础，在新版 XEngine 中补充程序回收和类型化表达式。最终原生提交以 `.github/native-dependencies.json` 为准。

| 版本 | 值 | 检查方式 |
| --- | --- | --- |
| xspcomm 包版本 | `0.3.0.dev3` | `importlib.metadata.version("xspcomm")`、`xspcomm.version()` |
| 原生 C++ ABI | `3` | `xspcomm.abi_version()`，对应 `libxspcomm.so.3` |
| coverage 描述协议 | `5` | `XTriggerEngine.CoverageVersion()` |
| pattern schema | `3` | 报告中的 schema version |

CI 从 `.github/native-dependencies.json` 获取精确提交与这三个原生依赖版本，构建明确标识为 `0.3.0.dev3` 的预览包并分别校验。Python 将程序统一编译为 XTriggerProgram，coverage 使用 XCoveragePoint 和嵌入式 bin.program；同一组可包含 Value、Pattern 和 Cross point。

在独立工作目录中取得上述 xcomm 提交后，使用独立 Python 环境构建：

```bash
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_XSPCOMM=0.3.0.dev3 \
BUILD_XSPCOMM_SWIG=python XSPCOMM_BUILD_WHEEL=1 \
python -m pip install /path/to/xcomm
python -m pip install -e '.[native,test]'
python -m pytest -q --require-xspcomm
python -m examples.coverage.trigger_bins --engine xcomm --strategy native
```

原生 ABI 变化后，picker 和生成的 DUT 扩展必须使用匹配的头文件与运行库重新构建。纯 Python MemoryBackend 仍不需要安装 xspcomm。真实 Adder DUT 的覆盖率示例见 `examples/coverage/picker_adder.py`：使用普通 Bundle 绑定生成的 XData，覆盖 128 位信号、跨采样 Sequence 和共享多终态 FSM。

取得使用配套 picker 重新生成的 Adder 包后，运行：

```bash
python -m examples.coverage.picker_adder \
  --dut-path /path/to/generated \
  --report /tmp/picker-adder-coverage.json
```

`--dut-path` 指向包含 Adder 包的目录。若构建时使用 `VERBOSE=ON` 保留了中间目录，再加 `--dut-module Adder.UT_Adder`。示例将每组输入保持两个采样，然后插入一个空闲采样；Adder RTL 本身是组合逻辑，Sequence/FSM 描述这里的观察窗口。9 个 bins 使用 5 份原生执行和一个采样注册，预期 carry 2 次、no_carry 2 次、完整溢出 Sequence 1 次、高半区结果 2 次；新增 129 位加法关系命中 12 次、128 位 signed-negative 命中 2 次。`repeated` bins 保留独立阈值，完整溢出的阈值 2 此时尚未达到。

## 尚未完成

配套草稿已补程序回收、有界缓存和两态 Bool/Bits、signed、宽运算、cast，具体规则与实测见[程序回收与表达式审查](coverage-v2-engine-audit.md)。需要同时构建 manifest 固定的新版原生草稿，单独使用上游基线 `e64d862` 不具备回收与类型化运算能力。X/Z 处理暂缓；不承诺完整 SystemVerilog 表达式语言或完整 UCIS 兼容。

动态 tag 捕获/关联、完成上下文及 temporal Cross 保留为后续讨论。当前示例按固定 tag 编译匹配；现有 Python TransactionCollector 仍处理动态事务关联。没有新增信号容器、事务引擎、时序 VM 或报告系统。


## 混合 point 与 pytrigger

`SignalCoverGroup` 可以同时声明值 point、`PatternCoverPoint` 和值 point 之间的 cross，
并将整个组绑定到同一时钟、采样阶段。`TemporalCoverPoint` 保留为 `PatternCoverPoint` 的别名。
值 point 按 normal/ignore/illegal/default 分类；pattern point 的各完成结果独立计数。
源码示例是 `examples/coverage/mixed_patterns.py`。

```python
fields = Fields(ProtocolBundle)

@covergroup(contract="interface/v1")
class InterfaceCoverage(SignalCoverGroup[ProtocolBundle]):
    valid = Level(source=fields.signal(lambda p: p.request.valid, value_type=bool))
    ready = Level(source=fields.signal(lambda p: p.response.ready, value_type=bool))
    responses = Responses()  # PatternCoverPoint，多个 bin 共用一个 FSM。
    valid_ready = Cross(valid, ready)
```

`Fields.signal` 复用已有 Bundle 字段链，声明采样结果为 int（默认）或 bool；bool 要求一位信号。
Bundle 注解提供属性补全。Bundle 允许动态属性，因此 selector 的拼写错误由声明阶段检查；
参数类型、输入根类型和 bin 引用另有静态类型检查。

`@pytrigger` 可直接声明 bin，支持 `with_args`、`bind` 和 enter/each_sample/change。
它仍可用于普通 await 和值 coverage 的采样 trigger。

```python
@pytrigger(mode=ConditionMode.EACH_SAMPLE)
def accepted(p: ProtocolBundle, *, minimum_tag: int = 0):
    return bool(p.request.valid.U()) and p.request.tag.U() >= minimum_tag

class Requests(PatternCoverPoint[ProtocolBundle]):
    all_requests = accepted
    large_tag = Bin.pattern(accepted.with_args(minimum_tag=2))
```

含 Python bin 的组必须声明显式、带版本的 `contract`，用于说明 Python 函数、闭包和外部模型的观察语义。
变更这些语义时应更新 contract。报告只保存函数标识和固定参数，不保存或恢复可执行 Python 代码。
同一 point 中复用同一 predicate 定义的 bins 每次采样只调用一次；不同闭包保留独立结果。

`SignalCoverGroup.bind` 默认使用 `strategy="auto"`。纯原生定义进入 C++；
包含 pytrigger bin 时，整个组使用现有 Python evaluator，并在 `sampling_backend` 和
`sampling_fallback` 中说明。显式 `strategy="native"` 会在采样前拒绝 Python bin。
组内的 xtrigger FSM 仍通过已有 Python 对照执行器保持相同计数与门控语义。
可将独立的高频原生 point 组织到另一个组，以保留其 C++ 批量执行效率。
Python predicate 必须同步返回；异常沿用 Execution 的资源和时钟清理路径。
