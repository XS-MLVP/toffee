# 2. 给输入，等时钟，检查结果

目标：把上一页的普通算式替换成一个有时钟的 DUT，检查“加上 3”和“空闲时不变”。

## 先写清楚这个 DUT 应该做什么

本教程的 8 位累加器初始值为 0，每个上升沿查看一次输入：

- `enable=1`：接受一个 `operand`，更新 `total = (total + operand) % 256`。
- `enable=0`：不接受输入，`total` 保持原值。
- 每次接受还会在 **两周期后**给出一个响应；本页先读 `total`，第 5 步再检查响应。

这里没有背压或 ready/valid 握手。`enable` 是这个练习 DUT 的明确输入规则，
不是 Toffee 对硬件协议的假设。把它保持为 1 两个上升沿，就会接受两次。

仓库提供 `examples/getting_started/toy_dut.py`，你可以直接使用 `TutorialDut()`。
它是 Python 写的教学替身，无需编译 RTL；初学时不用先理解它如何实现后端。
你需要知道的接口只有：

| 名称 | 用法 |
| --- | --- |
| `dut.operand.Set(3)` | 把操作数写成 3；输入范围 0～255 |
| `dut.enable.Set(1)` / `.Set(0)` | 允许 / 停止下一上升沿接受 |
| `dut.total.U()` | 读取当前无符号整数值 |
| `dut.clock` | 等待周期时使用的时钟 |
| `dut.backend` | 交给 Execution 推进这个 DUT |
| `dut.close()` | 释放这个教学环境 |

`dut` 是一个变量名；句点表示访问它的某个成员。`TutorialDut()` 每次创建一个全新的
累加器实例。这个“初值为零”由教学替身提供；真实设计的初始化要按其规格实现。

## 本节测试

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#clock -->
```python
import pytest

from examples.getting_started.toy_dut import TutorialDut
from toffee import ClockCycles, Execution


@pytest.mark.asyncio
async def test_one_input_and_idle():
    dut = TutorialDut()
    try:
        async with Execution(dut.backend):
            assert dut.total.U() == 0
            dut.operand.Set(3)
            dut.enable.Set(1)
            await ClockCycles(dut.clock, 1)
            dut.enable.Set(0)
            assert dut.total.U() == 3

            await ClockCycles(dut.clock, 2)
            assert dut.total.U() == 3
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_one_input_and_idle
```

预期 `1 passed`。

## 新出现的 Python 写法

`import` 从其他文件或包取得名字。`@pytest.mark.asyncio` 告诉 pytest 下面是一个
需要异步运行的测试；`async def` 允许函数里写 `await`。这里 **`await` 是等待仿真条件**，
不是让真实时间睡眠一秒。

`async with Execution(dut.backend):` 启动一个仿真环境，并在离开缩进块时退出它。
`await ClockCycles(dut.clock, 1)` 等待该时钟的下一个上升沿采样完成，随后才能读稳定结果。
没有这些等待，普通 Python 赋值本身不会让时钟前进。

`try ... finally` 表示：不管测试通过还是中途断言失败，都执行最后的 `dut.close()`。
本页先把这些环境代码写全，等它们重复出现时再介绍如何提取。

## 按周期看这段代码

| 时刻 | enable | 发生的事 | total |
| --- | --- | --- | --- |
| 创建 DUT | 0 | 初始状态 | 0 |
| 第一上升沿前 | 1 | 测试把 operand 写为 3 | 0 |
| 第一上升沿 | 1 | DUT 接受一次 3 | 3 |
| 第一上升沿后 | 0 | 测试撤下 enable | 3 |
| 第二、第三上升沿 | 0 | 没有新输入 | 3 |

读懂这个表，再做一次小实验：注释掉 `dut.enable.Set(0)`，重跑测试。
最后会得到 `assert 9 == 3`，因为 3 被加了三次。恢复该行后应重新通过。

这里的“周期”不是 Python 循环次数。后面的诊断还会显示 `tick`：当前后端一周期是
两个 half-tick，新时钟的第一上升沿为 tick 2。

[下一步：用多组数据检查零、最大值和溢出](more-cases.md)。
