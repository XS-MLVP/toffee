# 3. 从一个数字扩展到多组场景

目标：验证普通累加和边界。上一页通过只说明“加 3，再保持两拍”这一场景正确。

## 先列测试表，再写代码

| 输入序列 | 期望最后的 total | 检查的场景 |
| --- | --- | --- |
| 0 | 0 | 零也是合法输入，不能误当成“没有输入” |
| 3、5 | 8 | 状态能跨输入累加 |
| 255 | 255 | 最大输入值 |
| 255、1 | 0 | 刚好溢出到零 |
| 250、10 | 4 | 溢出后保留低 8 位 |

不要用 `dut.total.U()` 来计算期望值，那会让 DUT 和自己比较。这里的期望值来自上一页
写明的 8 位累加规则，可以手工算出来。

## 让 pytest 为每一行跑一次测试

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#cases -->
```python
@pytest.mark.asyncio
@pytest.mark.parametrize("operands, expected", [
    ([0], 0),
    ([3, 5], 8),
    ([255], 255),
    ([255, 1], 0),
    ([250, 10], 4),
])
async def test_accumulator_cases(operands, expected):
    dut = TutorialDut()
    try:
        async with Execution(dut.backend):
            for operand in operands:
                dut.operand.Set(operand)
                dut.enable.Set(1)
                await ClockCycles(dut.clock, 1)
                dut.enable.Set(0)
            assert dut.total.U() == expected
    finally:
        dut.close()
```

```bash
python3 -m pytest -v examples/getting_started/test_accumulator.py::test_accumulator_cases
```

应看到五条 `PASSED` 和 `5 passed`。`-v` 会列出每个用例，便于确认测试表的每一行都执行了。

`[3, 5]` 是列表，保存两个数；`for operand in operands:` 按顺序取出它们。
每次循环写一个输入，等一个上升沿，再撤下使能。

`@pytest.mark.parametrize("operands, expected", [...])` 可以理解为“用这张表反复调用下面
的测试函数”。表中每一行的两项分别传给 `operands` 和 `expected`。每次调用都新建 DUT，
因此不同用例不会继承前一次的累加值。

## 练习：添加一个能暴露问题的场景

在表中添加 `([128, 128], 0)`，预期变为 `6 passed`。
如果错误地写成 `([128, 128], 256)`，只有新增的用例失败；8 位寄存器存不下 256。

当前只检查了每组输入后的最终状态，还没有检查每次响应是否都正确、是否都到达。
后续会逐步补上这些检查。先观察代码里反复出现的四行输入动作：
写 operand、拉高 enable、等一拍、撤下 enable。

[下一步：把这个动作封装成 Driver](driver.md)。
