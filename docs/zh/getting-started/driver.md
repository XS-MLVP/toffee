# 4. 把重复的输入时序交给 Driver

目标：让测试写“加上 3”，把“哪些信号要写、保持多久、怎样回到空闲”集中定义一次。

前两步每次输入都写四行动作。如果接口改名或空闲值改变，散落在用例里的代码会很难维护。
Driver 就是这段**项目输入规则**的封装。

## 选择符合本例时序的 Driver

这个累加器恰好在下一个上升沿接受输入，因此可以使用已有的 `SyncSingleCycleDriver`。
继续在同一个主线文件中阅读 Driver 定义：

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#driver_setup -->
```python
from toffee import Bundle, SyncSingleCycleDriver


def encode_operand(operand):
    return {"enable": 1, "operand": operand}


def make_sync_driver(dut):
    return SyncSingleCycleDriver(
        dut.clock,
        Bundle(enable=dut.enable, operand=dut.operand),
        idle={"enable": 0, "operand": 0},
        encoder=encode_operand,
    )
```

`Bundle` 把两个信号放在一起，字段名是 `enable` 和 `operand`。
`{"enable": 1, "operand": operand}` 是 Python 字典，表示每个字段要写的值。

`encode_operand(3)` 返回 `{"enable": 1, "operand": 3}`。传入 `encoder=encode_operand`
时没有括号，是把函数交给 Driver，让它在每次发送时调用。`idle` 则明确说明一次输入后
把两个字段恢复为什么值。

`make_sync_driver(dut)` 是本教程的普通工厂函数：接收 DUT，返回一个配置好的 Driver 实例。
它不是框架要求的新类型。

## 用同样的输入再验证一次

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#driver -->
```python
@pytest.mark.asyncio
async def test_driver_restores_idle():
    dut = TutorialDut()
    driver = make_sync_driver(dut)
    try:
        async with Execution(dut.backend):
            async with driver:
                await driver.send(3)
                await driver.send(5)
                assert dut.total.U() == 8
                assert dut.enable.U() == 0

                await ClockCycles(dut.clock, 2)
                assert dut.total.U() == 8
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_driver_restores_idle
```

预期 `1 passed`。两个 `send` 接受 3 和 5；随后空闲两拍，结果仍然为 8。
`async with driver` 管理这里单独使用的 Driver，退出时释放它占用的输入信号。
第 6 步会把 Driver 放进 Agent，让 Execution 统一管理。

## send 到底等到了哪一步

`await driver.send(3)` 等到 **输入被接受**，返回接受事件；它没有等待两拍后的响应。
`Sync` 也不表示“阻塞整个 Python 进程”：它表示这条输入由调用它的协程等待完成。
本例必须写 `await`，只调用 `driver.send(3)` 不会完成发送。

把 `encoder` 的 enable 改成 0，再跑测试，会在期望 8 的地方读到 0：Driver 没有施加
DUT 能接受的输入。恢复为 1。

这个类适用于已经明确“一拍接受”的接口。若你的设计一次输入需要多拍或不同阶段，
应实现对应的 [SyncDriver / AsyncDriver](../guides/drivers.md)，明确返回真正的接受事件；
不能仅因为代码简短就套用 SingleCycle。那些场景在主线结束后有独立例子。

[下一步：捕获两拍后出现的输出](monitor.md)。
