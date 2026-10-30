# 8. 连续输入、多批次和未接受请求取消

目标：让输入继续向前走，不必等上一笔响应返回。
本例输入一拍接受，响应晚两拍，因此可以自然形成多个在途事务。

## 先分清两个等待

上一页连续写 `await agent.send(...)`，每次只等接受，所以已经能连续送入，
并没有等待每笔响应。现在用 `submit()` 一次提交多笔，并取得各自的响应句柄，
适合先把输入排好队、以后再用结果的用例。

| 调用 | 什么时候算这次等待完成 | 本教程的 Driver |
| --- | --- | --- |
| `await agent.send(3)` | 输入接受；接好检查器时仍会继续检查响应 | Sync 或 Async 都支持 |
| `transfer = agent.submit(3)` | 调用立即返回一个句柄；此刻不保证输入已接受 | 需要 Async Driver |
| `result = await transfer` | 这笔响应到达并检查成功 | submit 返回的句柄 |

`transfer` 可以理解为这笔请求的收据。以后等待同一张收据，拿到的是这一笔响应，
不会再发送一次输入。Sync Driver 的 Agent 不支持 `submit()`，需要换成能排队的输入 Driver。

## 接受规则不变，只换提交方式

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#pipeline_setup -->
```python
from toffee import AsyncSingleCycleDriver


def make_pipeline_agent(dut, *, response_timeout_cycles=3):
    driver = AsyncSingleCycleDriver(
        dut.clock,
        Bundle(enable=dut.enable, operand=dut.operand),
        idle={"enable": 0, "operand": 0},
        encoder=encode_operand,
    )
    return Agent(
        "accumulator",
        driver=driver,
        monitors={"response": make_response_monitor(dut)},
        response_monitor="response",
        clock=dut.clock,
        response_timeout_cycles=response_timeout_cycles,
    )
```

这次使用 `AsyncSingleCycleDriver`。`Bundle`、编码函数、idle 值和 Monitor 都与前面相同。
它内部按顺序驱动排队请求；用例无需创建 asyncio 任务、TaskGroup、锁或 `parallel`。

Async 不等于“所有信号可以同时写”。这个接口仍然每拍只能接受一笔，Driver 依次驱动；
输入排队与 DUT 内部同时处理多笔是两件事。

## 三笔请求连续进入

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#pipeline -->
```python
@pytest.mark.asyncio
async def test_three_inputs_in_flight():
    dut = TutorialDut(latency=2)
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(3)
            second = agent.submit(5)
            third = agent.submit(7)

            await agent.finish(timeout_cycles=10, observe_cycles=1)
            assert await first == 3
            assert await second == 8
            assert await third == 15
            assert dut.accepted_cycles == [1, 2, 3]
            assert dut.response_cycles == [3, 4, 5]
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_three_inputs_in_flight
```

预期 `1 passed`，而且测试真的检查了下表里的接受和响应周期：

| 上升沿 | 新接受的操作数 | 输出响应 | 仍未返回的请求 |
| --- | --- | --- | --- |
| 1 | 3 | 无 | 第 1 笔 |
| 2 | 5 | 无 | 第 1、2 笔 |
| 3 | 7 | 3 | 第 2、3 笔 |
| 4 | 无 | 8 | 第 3 笔 |
| 5 | 无 | 15 | 无 |

`assert await first == 3` 先取第一笔响应，再检查其数值。代码中把这些断言放在 finish
之后，只是为了展示每张收据对应的结果；即使不逐个 await，finish 也会检查后台失败。

做一次对照实验：紧接 `first = agent.submit(3)` 后插入 `assert await first == 3`。
第二笔会等到第一笔返回后才提交，连续接受的 `[1, 2, 3]` 断言应失败。恢复原例。
这说明**提交一笔就等响应**会把激励变成串行，单纯换成 Async Driver 并不能改变这种用法。

## 两批输入共用同一个环境

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#batches -->
```python
@pytest.mark.asyncio
async def test_two_batches_and_withdrawn_input():
    dut = TutorialDut()
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(3)
            withdrawn = agent.submit(99)
            withdrawn.cancel()
            await agent.drain(timeout_cycles=10)
            assert await first == 3

            second = agent.submit(5)
            await agent.finish(timeout_cycles=10, observe_cycles=1)
            assert await second == 8
            assert dut.operands == [3, 5]
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_two_batches_and_withdrawn_input
```

预期 `1 passed`。第一批的 99 在输入尚未被接受时撤销，不进入 DUT，也不推进模型。
`drain()` 等待调用前已提交的事务结束，之后还能提交第二批；最后才用 `finish()` 封闭提交。
Monitor 在整个环境内持续工作，不会在第一批完成后自动停止。

本例 `cancel()` 发生在任何 `await` 之前，所以 99 仍在队列中。请求一旦被接受就不能撤销，
再调用 `cancel()` 会报错，之后仍要等响应或超时。这个规则不会替代设计自己的取消/flush 协议。

## 接下来选哪个场景

主线继续 [看懂失败](failures.md)。如果你的响应可能乱序，选读
[按 tag 找回对应请求](out-of-order.md)。如果一次输入本身有多阶段，参考
[流水阶段重叠的完整例子](../guides/pipeline-examples.md)；那需要定义阶段资源，
不能通过把 SingleCycle 的并发参数调大来猜测时序。
