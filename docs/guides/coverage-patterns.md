# 统计跨周期事件

普通 bin 检查一次采样的值；时序 bin 检查一段时间内发生的事件。
例如“请求接收后四拍内返回”“某个条件连续保持两拍”，都可以复用 `@xtrigger`。
还不熟悉 Sequence 和 FSM 时，先看[编译条件与时序匹配](compiled-triggers.md)。

## 一个请求响应例子

下面的定义来自[完整示例](https://github.com/Makiras/xreactor/blob/main/examples/coverage/trigger_bins.py)。
`ProtocolBundle` 把已经绑定的请求、响应和时钟放在一起，不是另一种信号包装。

```python
from xreactor import RisingEdge, Sequence, Wait, Within, xtrigger
from xreactor.declarative import Bin, SignalCoverGroup, TemporalCoverPoint, covergroup
from xreactor.ir import signal_expr
from examples.coverage.declarative_protocol import ProtocolBundle


@xtrigger(sample=RisingEdge("clock"))
def roundtrip(pins: ProtocolBundle, *, tag: int = 1, maximum: int = 4):
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == tag)),
        Within(1, maximum,
               signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
               & (signal_expr(pins.response.tag) == tag)),
    )


class Completions(TemporalCoverPoint[ProtocolBundle]):
    tag_one = roundtrip
    tag_one_fast = Bin.pattern(roundtrip.with_args(maximum=2))


@covergroup(schema_id="protocol.completions", contract="roundtrip/v1")
class ProtocolCoverage(SignalCoverGroup[ProtocolBundle]):
    completions = Completions()
```

`tag_one` 使用默认的四拍窗口，`tag_one_fast` 使用两拍窗口。
同一次三拍后返回的响应只命中前者。计数名来自类属性，查询时也使用这个声明：
`coverage.completions.count(Completions.tag_one)`。

创建实例后，用 `bind(pins=..., sample=RisingEdge(clock), strategy="native")`
绑定项目的引脚和时钟，再把 `coverage.runtime` 交给 `Execution` 管理。
需要由 Monitor 计算出的延迟、编号等属性时，使用[事务覆盖](coverage-transactions.md)。

## 参数和不同终态

`roundtrip(pins, maximum=3)` 创建可等待的 Trigger。
`roundtrip.with_args(maximum=3)` 则保存参数，留到绑定信号时使用。
有必填参数时，不能省略这一步。

一个 FSM 可以有 `OK` 和 `BAD_DATA` 等终态。
用 `Bin.pattern(definition, terminals=("OK",))` 选择要计数的结果，
再用 `Bin.illegal(...)` 把错误结果定义为非法命中。
到达未选择的终态同样会结束该次匹配，只是不增加这个 bin 的计数。

同一覆盖点内，相同过程可供多个终态 bin 共用执行状态，计数仍分开。
窗口或参数不同的过程分别执行。`compile().explain()` 可以查看共享关系。

## 多笔请求可能重叠时

默认只跟踪一个活动匹配。需要并发跟踪时，显式设置容量：

```python
Bin.pattern(roundtrip, overlap=True, max_active=8)
```

同一拍完成三次匹配就计三次。容量不足会报错并标记采集不完整，不能静默漏数。
这不会自动完成任意动态编号的请求响应关联。

`TemporalCoverPoint` 之间不能直接建 Cross，因为两个过程可能在不同时间完成。
先在 Monitor 中形成一条完成事务，再对事务字段做 Cross，含义会更清楚。

## 执行和结束

`strategy="native"` 要求原生执行，能力不足时直接报错。
`"auto"` 允许回退到 Python，报告会注明策略和原因。
原生依赖与类型限制见[表达式与资源管理](expressions-and-resources.md)。

普通 `await` 的取消不会清空覆盖组自己的匹配历史。
退出管理它的 `Execution` 可结束采样；清除历史需要调用覆盖组的相应操作。

## 运行并读懂结果

```bash
python3 -m examples.coverage.trigger_bins --engine memory --strategy python
python3 -m examples.coverage.trigger_bins --engine xcomm --strategy native
```

示例发送编号 1、2、3，响应按 2、1、3 返回。

| 覆盖项 | 次数 | 原因 |
| --- | ---: | --- |
| `requests.handshake` | 3 | 接收三笔请求 |
| `completions.tag_one` | 1 | 编号 1 在四拍窗口内返回 |
| `completions.tag_one_fast` | 0 | 没落入两拍窗口 |
| `completions.tag_two_ready` | 1 | 编号 2 响应后，ready 连续保持两拍 |
| `completions.correct_data` | 1 | 编号 1 的响应数据正确 |
| `completions.bad_data` | 0 | 没观察到错误数据 |

未命中项可能只是本次没有相应场景。设计是否正确仍由断言和检查器判断。
