# Coverage v2 多周期与协议绑定示例

状态：实验分支示例。[完整可运行代码](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/examples/coverage/declarative_protocol.py)从同一个 ready/valid 请求响应接口展示三种覆盖：相邻时序 bin、多周期完成时采样，以及按事务 ID 关联后的延迟与乱序覆盖。当前配套引擎版本见 [picker/xcomm 适配](coverage-v2-trigger-bins.md#pickerxcomm)。

## 多周期 coverpoint

```python
@coverpoint(overlap=OverlapPolicy.ALLOW)
class ReadyPoint(CoverPoint[bool]):
    stalled = Bin.values(False)
    accepted = Bin.values(True)
    waited_two_then_accepted = Bin.transition(False, False, True)
```

`waited_two_then_accepted` 在三个连续有效采样周期观测到 ready=0、0、1 时命中一次，计在最后一拍。前两个周期同时累计 stalled，最后一拍同时累计 accepted。这些 bins 可以重叠命中，Cross 使用完成采样时实际命中的 normal bins。

请求组每个上升沿采样，并用 request.valid 作 group gate。valid=0 会断开 transition 历史，不能跨过无效周期拼接一个命中。native 路径把这个 transition 交给现有 C++ 覆盖引擎保存历史和计数。

## 类型化引脚组与覆盖模型绑定

示例的 `RequestBundle`、`ResponseBundle` 和 `ProtocolBundle` 直接继承现有 Bundle，只添加项目字段注解。叶节点的 Pin 是具有 W、U、Set 方法的静态协议，实际对象仍是原始 XData；不再定义额外的 Pins 容器或进行 Pins→Bundle 转换。clock 作为采样源元数据保存在 ProtocolBundle，允许使用 XClock 或 MemoryBackend token，不加入要 sample/drive 的数据字段。

Bundle 的字段注解提供补全，不会自动产生形状检查或表达式运算。真实绑定、遍历、sample 和 drive 仍由已有 Bundle 完成。需要表达式时使用已有 `signal_expr`，其接受符号节点时原样返回，从而同一个表达式构造可以用于 @xtrigger 和已绑定的原始信号。

逻辑观测使用 frozen dataclass `CycleSnapshot`，包含嵌套的 request 和 response。绑定关系在同一处显式列出：

```python
cycle_fields = Fields(CycleSnapshot)

@covergroup(
    schema_id="protocol.request",
    contract="protocol.valid-request-cycles/v1",
    iff=Iff.truth(cycle_fields.select(lambda s: s.request.valid)),
)
class RequestCycleCoverage(CoverGroup[CycleSnapshot]):
    ready = ReadyPoint(source=cycle_fields.select(lambda s: s.request.ready))
    opcode = OpcodePoint(source=cycle_fields.select(lambda s: s.request.opcode))
    ready_opcode = Cross(ready, opcode)

request.bind(
    trigger=RisingEdge(pins.clock),
    fields=(
        wire(cycle_fields.select(lambda s: s.request.valid), pins.request.valid,
             source_id="dut.request.valid"),
        wire(cycle_fields.select(lambda s: s.request.ready), pins.request.ready,
             source_id="dut.request.ready"),
        wire(cycle_fields.select(lambda s: s.request.opcode), pins.request.opcode,
             source_id="dut.request.opcode"),
    ),
    strategy="native",
)
```

Sample 字段、引脚组属性和 point 引用都有明确名称。source_id 是报告交换使用的外部身份。时钟路径使用已绑定信号采集；coverage 引擎自身的 native 采样不构造 CycleSnapshot，输入类型约束在字段和物理位宽绑定时检查。示例中的事务 Monitor 仍在 Python 中逐拍构造快照，以完成动态 tag 关联。

## 多周期 Sequence 完成时采样

```python
@xtrigger(sample=RisingEdge("clock"))
def tag_one_roundtrip(pins, *, maximum=4):
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == 1)),
        Within(1, maximum,
               signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
               & (signal_expr(pins.response.tag) == 1)),
    )

completed.bind(
    trigger=tag_one_roundtrip(pins, maximum=4),
    fields=(
        wire(cycle_fields.select(lambda s: s.response.tag), pins.response.tag,
             source_id="dut.response.tag"),
        wire(cycle_fields.select(lambda s: s.response.data), pins.response.data,
             source_id="dut.response.data"),
    ),
    strategy="native",
)
```

这个 Sequence 在请求 tag=1 被接受后，等待接下来 1 至 4 拍内同 tag 的响应握手。完成时，`RoundTripCoverage` 的 tag、data 和 Cross 使用响应完成那一拍的真实引脚值，累计一个 sample。其他 tag 的响应不能完成它，超出窗口也不计数。

Sequence 是这个 group 的采样条件。相邻 transition 是 bin 的 matcher。两者的职责明确区分；当前 API 尚未提供每个 bin 独立声明任意 Sequence/FSM 的能力。这个固定 tag 的 Sequence 也不承担任意动态 tag 的事务关联。

后续[共享时序程序设计](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/design/drafts/coverage-v2-patterns.md)将每个 bin 复用 xtrigger 模式、原生捕获及动态 key 关联列为 C++ 扩展需求。目标接口按覆盖语义设计；本例最初展示的 coverage 描述协议 3 能力是实现起点。

绑定导出包含时序程序、采样阶段和窗口参数。改变 Within 的 maximum 会改变采样契约，旧报告与新窗口的报告不能合并；仅改变 Python/native 执行策略不会改变同一采样契约。无法序列化的 Python predicate 等来源要求显式的项目观察契约。

## Monitor 事务流与覆盖模型绑定

```python
live = pins
monitor = SamplingMonitor(
    RisingEdge(pins.clock),
    capture=lambda _: pins.capture(live.sample()),
).start(execution)

observation = await monitor.recv()  # Transfer[CycleSnapshot]
completed = collector.observe(observation)
if completed is not None:
    transactions.sample(completed)  # CompletedTransaction
```

`capture` 在稳定采样点同步保存不可变快照，handler 延后执行也不会读到下一拍的引脚。Collector 从 valid & ready 确认请求和响应握手，用 tag 保存和查找 pending 事务，从 Transfer 的 accepted/response tick 推导延迟。示例的两个半周期 tick 构成一个时钟周期。

`CompletedTransaction` 保存 tag、opcode、latency、correct、out_of_order 及起止 tick。`TransactionCoverage` 对这些真实派生的事务字段分桶，并定义 opcode × latency Cross。它在响应完成后采样一次；没有响应就不会产生完成事务。

这里的 latency point 是事务属性覆盖，跨周期关联由 collector 完成。它和 C++ 中保存历史的 transition bin 是两种不同实现路径。动态 ID 关联和响应内容比较在 Python observer 中进行，样本完成后进入现有事务覆盖计数。

Monitor 使用单一消费入口。覆盖率和其他事务消费者需要在这个入口显式分发同一 observation，不能让两个任务同时 recv 同一个队列。示例用 TaskGroup 管理驱动和消费任务，用 Monitor/Driver 上下文释放订阅和引脚所有权；丢响应、重复 tag、未知响应和错误采样阶段会明确失败。

## 可以核对的运行结果

本地 DUT substitute 的实际握手轨迹如下，模型逻辑根据接受请求调度响应；collector 只读取观测快照：

| 周期 | 请求 | 响应 | 完成事务 |
| --- | --- | --- | --- |
| 1 | tag 1 被阻塞 | 无 | 无 |
| 2 | tag 1 被阻塞 | 无 | 无 |
| 3 | 接受 tag 1 | 无 | 无 |
| 4 | 接受 tag 2 | 无 | 无 |
| 5 | 接受 tag 3 | tag 2 | 延迟 1，乱序 |
| 6 | 无 | tag 1 | 延迟 3 |
| 7 | 无 | tag 3 | 延迟 2 |

请求覆盖有 5 个有效周期 sample，三拍 transition 命中 1 次；tag 1 的 Sequence 完成采样 1 次；事务覆盖有 3 个 sample，延迟 one_cycle=1、two_or_three=2，乱序 yes=1。把窗口上限改为 2 后，tag 1 的 Sequence 命中为 0，事务覆盖仍有 3 个完成事务。

## 运行和验证

在临时实验仓库根目录：

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src:/tmp/xreactor-coverage-native-build/python"

python3 -B examples/coverage/declarative_protocol.py --engine memory --strategy python
python3 -B examples/coverage/declarative_protocol.py --engine xcomm --strategy python
python3 -B examples/coverage/declarative_protocol.py --engine xcomm --strategy native \
  --output /tmp/xreactor-coverage-v2-ob2uhbua/artifacts/protocol-native

python3 -B examples/coverage/declarative_protocol.py --engine xcomm --strategy native --maximum 2
python3 -B -m pytest tests/coverage/test_declarative_protocol.py -q -p no:cacheprovider
```

生成产物包含逐周期快照、请求接受周期、关联完成事务、三类覆盖报告和实际引脚/时序绑定契约。[行为测试](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/tests/coverage/test_declarative_protocol.py)比较 memory/Python、xcomm/Python、xcomm/native 三条路径，包含窗口超时、错误响应、丢响应、tag 同拍复用及清理。[类型正例](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/tests/typing/coverage_v2_protocol_positive.py)验证嵌套字段、引脚引用和 Monitor → collector → transaction coverage 的类型链路。
