# 按事务统计延迟与乱序

“ready 连续低了两拍”是信号上的时序；“编号 7 的请求等了三拍才返回”是事务的属性。
前者只需观察连续采样，后者还要记住请求，并按编号找回对应响应。
不要把这两件事混成一个计数。

本页使用[请求与响应示例](https://github.com/XS-MLVP/toffee/blob/main/examples/coverage/declarative_protocol.py)，
沿用[类声明](coverage-classes.md)中的写法。

## 观察连续采样

```python
from toffee.declarative import Bin, CoverPoint, OverlapPolicy, coverpoint


@coverpoint(overlap=OverlapPolicy.ALLOW)
class ReadyPoint(CoverPoint[bool]):
    stalled = Bin.values(False)
    accepted = Bin.values(True)
    waited_two_then_accepted = Bin.transition(False, False, True)
```

连续三次有效采样为 `False, False, True` 时，最后一个 bin 命中一次。
前两次也命中 `stalled`，最后一次也命中 `accepted`，因此显式允许重叠命中。

示例只在请求有效时采样。无效周期会打断 transition 历史，不能跨过空闲周期拼接命中。
这种覆盖不能说明某个请求是否收到了正确响应。

## 在多周期条件完成时采样

组的采样条件不一定是每个时钟沿，也可以是一个 Sequence：

```python
from toffee import RisingEdge, Sequence, Wait, Within, xtrigger
from toffee.ir import signal_expr


@xtrigger(sample=RisingEdge("clock"))
def tag_one_roundtrip(pins, *, maximum=4):
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == 1)),
        Within(1, maximum,
               signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
               & (signal_expr(pins.response.tag) == 1)),
    )
```

这里等待编号 1 的请求被接收，再等待随后 1～4 拍内同编号的响应被接收。
把它作为 `coverage.bind(trigger=...)` 的采样条件后，字段取自响应完成那一拍。
其他编号不能完成这个 Sequence，超出窗口也不会命中。

这是固定编号的匹配，不会自动记住任意请求编号。
若要让每个 bin 使用各自的 Sequence 或 FSM，使用[时序覆盖点](coverage-patterns.md)。

## 任意编号由谁关联

示例中的 Monitor 在稳定采样点保存请求和响应，Collector 再按以下顺序处理：

1. 请求握手时，保存编号、操作码和接收时刻。
2. 响应握手时，用编号找回原请求。
3. 计算延迟，检查数据，并判断是否乱序完成。
4. 生成一条 `CompletedTransaction`，对这条完成事务采样一次。

`TransactionCoverage` 对操作码、延迟、数据是否正确、是否乱序分桶，
也可以统计操作码与延迟的交叉覆盖。关联过程在 Python 中进行，不由原生 bin 自动完成。

Monitor 只有一个消费入口。需要同时检查与采样覆盖率时，在该入口处分发同一条记录，
不要让两个任务竞争 `recv()`。保存的是采样时的快照，不能等异步处理时再重读引脚。

## 不要用覆盖率代替检查

没有响应就不会产生完成事务，也就没有延迟样本。
“正确数据”bin 命中，不代表其他请求都返回了正确结果。
漏响应、错误数据、未知编号和重复响应仍应由检查器或 Scoreboard 报错。

调整窗口长度、采样条件或字段含义后，不能直接合并旧结果。
只切换 Python 或原生执行策略，不会改变同一组数据的含义。

## 运行示例

```bash
python3 -m examples.coverage.declarative_protocol --engine memory --strategy python
python3 -m examples.coverage.declarative_protocol --engine xcomm --strategy native \
  --output output/transactions
```

示例输出请求、完成时采样和事务覆盖三类结果。
先比较它们各自在数什么，再把相同结构用到项目自己的 Monitor 和检查器中。
