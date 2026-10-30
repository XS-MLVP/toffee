# 选读：响应没有按输入顺序回来

前置：已经完成 [流水提交](pipeline.md)。目标是理解为什么某些接口需要用编号匹配响应。

主线的响应严格按接受顺序返回，FIFO 就够了。如果先收到第二笔响应，把它拿去和第一笔
期望比较会误报。此时请求和响应必须提供能对应起来的 key，例如 tag；框架不能凭数值猜测。

## 这次用一个带 tag 的累加器变体

仓库的 `examples/transactions/reference_model.py` 提供完整变体。本页显式换用它，
不修改主线的 `TutorialDut`。变体接收正操作数，输入的 tag 为 1、2、3；tag=0 是空闲，
本例不会触及位宽溢出。每笔结果按**输入接受顺序**计算，但响应延迟故意不同：

| tag | operand | 期望累加值 | 接受周期 | 响应延迟 | 响应周期 |
| --- | --- | --- | --- | --- | --- |
| 1 | 3 | 3 | 1 | 4 | 5 |
| 2 | 5 | 8 | 2 | 2 | 4 |
| 3 | 7 | 15 | 3 | 3 | 6 |

因此响应 tag 顺序是 2、1、3。期望累加值仍是 3、8、15，模型不能改成按完成顺序相加。

`Command(tag=1, operand=3)` 是含有两个字段的请求对象；`Result(tag=1, total=3)`
是响应对象。变体用 `@dataclass(frozen=True)` 定义这些记录，Python 自动生成构造和相等比较，
`frozen=True` 防止创建后改字段。它们是普通数据，不会自行推进仿真。

## 怎样把 key 告诉 Agent

变体的 `AccumulatorAgent` 构造器除了 Driver、Monitor、clock 和预算，还设置了
`request_key=lambda request: request.tag` 与 `response_key=lambda response: response.tag`。
`lambda request: request.tag` 是一个小函数，意思是“给我请求，我返回它的 tag”。
两端 key 一样才属于同一笔事务；不能同时让两笔在途请求使用相同 tag。

变体的 Driver 明确写入 tag 和 operand，等一拍后把输入恢复为空闲；Monitor 捕获包含 tag
的响应记录。完整实现可以打开仓库中的文件，或查看
[源码](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/reference_model.py)。
主线已逐步说明了这些组件的职责，这里复用现成的接口实现。

## 运行并验证乱序

文件：`examples/getting_started/test_tutorial_out_of_order.py`

<!-- executable-example: examples/getting_started/test_tutorial_out_of_order.py -->
```python
import pytest

from examples.transactions.reference_model import (
    AccumulatorAgent, AccumulatorDut, AccumulatorModel, Command, Result,
)
from toffee import Execution


@pytest.mark.asyncio
async def test_tagged_responses_arrive_out_of_order():
    dut = AccumulatorDut("memory")
    agent = AccumulatorAgent(dut, response_timeout_cycles=5)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(Command(tag=1, operand=3))
            second = agent.submit(Command(tag=2, operand=5))
            third = agent.submit(Command(tag=3, operand=7))
            await agent.finish(timeout_cycles=20, observe_cycles=1)

            assert dut.emitted_tags == [2, 1, 3]
            assert await first == Result(tag=1, total=3)
            assert await second == Result(tag=2, total=8)
            assert await third == Result(tag=3, total=15)
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_tutorial_out_of_order.py
```

预期 `1 passed`。`first` 仍然取回 tag=1 的响应；它不表示“第一条到达的响应”。
三个句柄按提交顺序等待，也不会把已发生的物理响应顺序改回 FIFO。

练习：把 `dut.emitted_tags` 的期望顺序改成 `[1, 2, 3]`，应当失败，说明本例确实发生了乱序。
恢复顺序后，再把第一笔期望改成 `Result(tag=1, total=8)`，则会在结果断言失败。

只有接口真的携带对应编号时才能用这种方案。如果无编号而输出顺序又不确定，需要先讨论
设计的可观察关联规则，不能随便添加 Python key 冒充硬件信息。

[接入 native 和项目 DUT](native-and-next.md) · [返回教程路线](index.md)。
