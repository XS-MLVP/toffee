# 6. 用一个 Agent 实例封装接口

目标：让测试不再逐个启动组件、手动接收和比较每个响应。
前面已经分别写出了 Driver 和 Monitor，现在把它们放进同一个接口实例。

## 这个实例里装什么

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#agent_setup -->
```python
from toffee import Agent


def make_agent(dut):
    return Agent(
        "accumulator",
        driver=make_sync_driver(dut),
        monitors={"response": make_response_monitor(dut)},
        response_monitor="response",
        clock=dut.clock,
        response_timeout_cycles=3,
    )
```

`make_agent(dut)` 复用前两页的函数，返回一个 Agent。字典里的 `"response"` 是这个
Monitor 的名字；`response_monitor="response"` 把它接到 Agent 的响应检查器。
因此后面不要再自己消费这个 Monitor，否则会和检查器抢同一条响应。

`clock` 指定预算使用哪个时钟，`response_timeout_cycles=3` 要求每笔输入接受后
最多三周期内给出响应。本例规格的延迟为两周期，所以三周期是我们为练习显式设置的上限，
不是框架猜测的延迟。默认按接受顺序逐个匹配响应，称为 FIFO 匹配。

关系可以读成两条路径：

```text
用例 --send(操作数)--> Agent 内的 Driver --> DUT 输入
DUT 响应 --> Agent 内的 Monitor --> 检查器 <-- 用例给出的 expected
```

## 第一个自动检查的 Agent 用例

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#agent -->
```python
@pytest.mark.asyncio
async def test_agent_checks_responses():
    dut = TutorialDut()
    agent = make_agent(dut)
    try:
        async with Execution(dut.backend, agents=[agent]):
            await agent.send(3, expected=3)
            await agent.send(5, expected=8)
            await agent.finish(timeout_cycles=10, observe_cycles=1)
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_agent_checks_responses
```

预期 `1 passed`。`agent` 是普通实例；创建它不会开始推进时钟。
进入 `Execution(..., agents=[agent])` 后，环境启动它的组件；离开时统一清理。
无需给 Agent 写 `async with`，用例也不必拆开 Agent 去操作里面的 Driver 和 Monitor。

这里的 `send()` 仍等输入接受。因为 Agent 已经接好了检查器，所以即使发送返回了，
后续响应也会继续自动检查；这与单独使用 Driver 时只有输入动作不同。

## 为什么最后要 finish

`finish(timeout_cycles=10, observe_cycles=1)` 做三件事：停止接收新提交，等待之前请求的
响应检查完成，再多看一周期是否有额外响应。整个过程最多允许十周期。
退出环境负责清理；它不会替你无限等待尚未返回的响应。

| 参数 | 从什么时候开始算 | 用于什么 |
| --- | --- | --- |
| `response_timeout_cycles=3` | 每笔输入被接受时 | 限制这一笔响应最晚何时到 |
| `finish(timeout_cycles=10)` | 调用 finish 时 | 限制这次排空和观察的总等待 |
| `observe_cycles=1` | 已提交的事务排空后 | 再观察一拍，检测例如多出来的输出 |

有限观察窗口只能检查它覆盖的时间。真实项目需要根据设计约定选择观察长度，不能认为
观察一拍就证明以后永远不会再有输出。

## 亲手触发一次自动检查失败

把第二次发送的 `expected=8` 改成 9，运行会看到 `ScoreboardMismatch`，包含
`expected: 9`、`actual: 8`。即使测试没有手写 `assert`，后台检查失败也会使 pytest 失败。
改回 8 后继续。

Agent 负责接口的组织，期望值仍然来自用例。长序列手写每个结果很容易错，
[下一步把规格写成独立参考模型](reference-model.md)。
