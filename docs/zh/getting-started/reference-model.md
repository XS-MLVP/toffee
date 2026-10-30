# 7. 自动算期望值，让多个用例共用环境代码

目标：减少手算长序列的负担，并把已经反复出现的创建、启动、清理集中到测试环境里。
这一步只新增两个概念：参考模型和 pytest fixture。

## 把规格写成一个小模型

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#model -->
```python
class AccumulatorModel:
    def __init__(self):
        self.total = 0

    def accept(self, operand):
        self.total = (self.total + operand) % 256
        return self.total
```

`class` 定义一种对象。调用 `AccumulatorModel()` 创建它，`__init__` 设置初始状态。
`self` 表示这个对象自身；`self.total` 是模型自己的累加值。
`accept(operand)` 按规格更新它，并返回这笔输入的期望响应。

模型不读取 DUT，不等待时钟，也不包含 Driver；它可以在普通 Python 中单独运行：

```python
from examples.getting_started.test_accumulator import AccumulatorModel

model = AccumulatorModel()
assert model.accept(250) == 250
assert model.accept(10) == 4
```

在环境启动前调用 `agent.connect(model)`，Agent 就会在**输入实际被接受后**推进模型。
模型按接受顺序更新，不按响应到达顺序更新。请求还在提交队列里时，不应提前修改模型。
用例连接模型后可以只写 `send(3)`，不再逐笔填写 `expected`。

## 两个独立用例，共用一份环境定义

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#fixture -->
```python
import pytest_asyncio


@pytest_asyncio.fixture
async def accumulator():
    dut = TutorialDut()
    agent = make_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            yield agent
    finally:
        dut.close()


@pytest.mark.asyncio
async def test_model_checks_a_sequence(accumulator):
    for operand in [3, 5, 7]:
        await accumulator.send(operand)
    await accumulator.finish(timeout_cycles=10, observe_cycles=1)


@pytest.mark.asyncio
async def test_model_checks_wraparound(accumulator):
    for operand in [250, 10]:
        await accumulator.send(operand)
    await accumulator.finish(timeout_cycles=10, observe_cycles=1)
```

```bash
python3 -m pytest -v examples/getting_started/test_accumulator.py -k test_model_checks
```

`-k test_model_checks` 选择名称包含这段文字的两个测试；其他用例显示为 `deselected`。
预期 `2 passed`。第一条用例自动检查 3、8、15，第二条检查 250、4。
第二条不会从上一条的 15 开始，因为 fixture 默认每个测试重新执行一次。

## fixture 怎样把 Agent 交给测试

`@pytest_asyncio.fixture` 把 `accumulator()` 登记为环境准备函数。测试形参也叫
`accumulator`，pytest 就会先运行这个 fixture，把 `yield agent` 里的对象传给测试。
因此测试里的 `accumulator.send(...)` 就是在调用刚才创建的 Agent。

```text
创建 DUT、模型和 Agent
    ↓
进入 Execution
    ↓
yield agent → 执行一条测试 → 测试结束或抛出异常
    ↓
退出 Execution，再 dut.close()
```

`yield` 在这里是把环境暂时交给测试，等测试结束后再继续执行清理代码。
`finish` 保留在用例中，是因为每个场景需要的排空预算和观察长度可能不同。
实例不能关闭后重启；每个测试新建环境是本教程采用的方式。

## 练习：给模型留一个可发现的错误

临时删掉模型里的 `% 256`，重新运行两个用例。第一条仍通过，第二条应报告期望 260、
实际 4。此时有错的是模型；DUT 仍遵循 8 位规则。恢复取余后两条都通过。

模型正确性也需要用手算的短例子验证，不能因为它叫 reference 就默认绝对正确。
教学 DUT 内部通过输入历史求和，模型通过逐笔更新状态实现；两边没有互相调用。

[下一步：连续提交，让多笔请求同时在途](pipeline.md)。
