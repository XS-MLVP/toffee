# 5. 输出晚两拍，而且只出现一拍

目标：区分“此刻读到的信号”和“之前某一拍采到的结果”。
上一页检查的是内部可见的 `total`。实际接口经常只提供短暂的响应，需要按它出现的时刻采样。

## 补上响应规则

本例 `TutorialDut(latency=2)` 在接受后的两周期给出结果。`dut.response` 有两种状态：
整数（包括 0）表示这拍有响应，`None` 表示没有响应。这个 Python 属性是教学替身提供的
简化观察接口；真实 DUT 需要按其端口和有效条件定义采样。

| 上升沿 | 输入动作 | response |
| --- | --- | --- |
| 第 1 拍 | 接受 3 | None |
| 第 2 拍 | 空闲 | None |
| 第 3 拍 | 空闲 | 3 |
| 第 4 拍 | 空闲 | None |

如果第 4 拍才去读 `dut.response`，3 已经消失。Monitor 的作用是在第 3 拍把它保存下来。

## 明确“何时采”和“采什么”

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#monitor_setup -->
```python
from toffee import PythonPredicateTrigger, RisingEdge, SamplingMonitor


def make_response_monitor(dut):
    def response_present():
        return dut.response is not None

    def capture(event):
        return dut.response

    return SamplingMonitor(
        PythonPredicateTrigger(
            "response-present", response_present,
            sample=RisingEdge(dut.clock), mode="each_sample",
        ),
        capture=capture,
    )
```

这里两件事分开定义：

- `response_present()` 判断当前是否有响应。`is not None` 很重要：响应 0 仍然是有效结果。
- `capture(event)` 在事件发生时返回响应整数。整数是不可变的，保存后不会随着 DUT 改变。

函数写在 `make_response_monitor` 里面，是为了能使用这次传入的 `dut`。
`PythonPredicateTrigger` 在每个 `RisingEdge` 调用判断函数；`mode="each_sample"`
表示每一拍条件成立都采，连续两拍有响应时也不会漏掉第二拍。

`capture` 没有 `async` 和 `await`：它应当当场保存数据。
复杂接口可以用 `Bundle.sample()` 保存结构快照，不能只把会继续变化的 DUT 对象交出去。

## 故意晚读，确认 Monitor 保存了那一拍

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#monitor -->
```python
from toffee import Scoreboard


@pytest.mark.asyncio
async def test_delayed_response_snapshot():
    dut = TutorialDut(latency=2)
    driver = make_sync_driver(dut)
    monitor = make_response_monitor(dut)
    checker = Scoreboard("accumulator")
    try:
        async with Execution(dut.backend) as execution:
            async with driver, monitor.start(execution):
                await driver.send(3)
                assert dut.response is None
                await ClockCycles(dut.clock, 3)
                assert dut.response is None

                observed = await monitor.recv()
                checker.check(expected=3, actual=observed.value)
                assert observed.event.tick == 6
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_delayed_response_snapshot
```

预期 `1 passed`。这里发送发生在第 1 拍，又等待 3 拍后已到第 4 拍。
`dut.response` 此刻是 None，但 `monitor.recv()` 从缓存取出第 3 拍的记录。
`observed.value` 是保存的整数 3；`observed.event.tick` 是采样时刻 6，不是读取记录时的时刻。

`as execution` 为已经启动的环境取一个名字，用它启动 Monitor。
`async with driver, monitor.start(execution)` 相当于两个嵌套的管理块；退出时先关闭 Monitor，
再关闭 Driver。Monitor 在发送前启动，才能看到之后的全部响应。

## Scoreboard 在这里做了什么

`checker.check(expected=3, actual=observed.value)` 做一次直接比较，失败会带上
期望值、实际值和差异信息。这时我们仍手动取出每个响应，没有自动关联事务或设置响应超时。
如果删除响应，单独的 `recv()` 不会凭空知道等了多久才算失败。
下一步交给 Agent 接好检查链，并给每笔响应明确预算。

练习：把 `latency=2` 改成 1。数值检查仍正确，但时间检查会变为 `assert 4 == 6`。
如果规格确实改成了一拍响应，时间预期也应改成 4；先恢复原例再继续。

[下一步：把 Driver 和 Monitor 封装成 Agent](agent-test.md)。
