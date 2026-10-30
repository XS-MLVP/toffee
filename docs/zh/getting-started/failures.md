# 9. 把失败当作可以解释的结果

目标：亲手运行四种错误，知道检查器在报告什么。这里的命令**应该失败**，不是安装失败。

## 先确认正常例子通过

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_three_inputs_in_flight
```

看到 `1 passed` 后，再运行错误练习。仓库的 `failure_lab.py` 使用同一套 Driver、Monitor
和模型，只让教学 DUT 故意算错、漏回、迟到或多回一次。它不以 `test_` 命名，
所以普通目录测试不会自动运行它。

文件：`examples/getting_started/failure_lab.py`

<!-- executable-example: examples/getting_started/failure_lab.py -->
```python
"""Intentionally failing exercises; run one case explicitly with pytest -k.

This filename does not start with test_, so the regular suite skips it.
"""

import pytest

from examples.getting_started.test_accumulator import AccumulatorModel, make_pipeline_agent
from examples.getting_started.toy_dut import TutorialDut
from toffee import Execution


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong", "missing", "late", "extra"])
async def test_find_the_fault(fault):
    dut = TutorialDut(fault=fault)
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            agent.submit(3)
            await agent.finish(timeout_cycles=10, observe_cycles=2)
    finally:
        dut.close()
```

这一次没有保存 `agent.submit(3)` 的返回句柄，也没有等待某一笔 transfer；
`finish()` 仍然必须报告这些后台失败，不能因为没人 await 那张收据就漏掉错误。

## 练习一：计算结果错误

```bash
python3 -m pytest -q examples/getting_started/failure_lab.py -k wrong --tb=short
```

`-k wrong` 只选择名字带 wrong 的那一项；`--tb=short` 缩短调用栈。
预期 `1 failed, 3 deselected`，关键诊断如下（省略了栈帧和部分字段）：

```text
ScoreboardMismatch: accumulator mismatch
  request: 3
  accepted_tick: 2
  observed_tick: 6
  expected: 3
  actual:   4
```

先读 request、expected、actual，知道哪笔操作不符；再看两个 tick，确认采样时间。
不要先钻进所有框架栈帧。这里故障把响应加了 1，因此改大 timeout 无法修复。

## 练习二、三：漏响应与迟到

分别运行：

```bash
python3 -m pytest -q examples/getting_started/failure_lab.py -k missing --tb=short
python3 -m pytest -q examples/getting_started/failure_lab.py -k late --tb=short
```

每条都应 `1 failed, 3 deselected`，异常为 `ScoreboardTimeoutError`。
本例在 tick 2 接受，响应预算三周期，截止为 tick 8；检查在之后的半周期屏障报告
`deadline exceeded at tick 9`。tick 8 当拍正确返回的响应仍会被接受，不会提前误判超时。

missing 完全不返回；late 要等五周期，已经超过约定。**仅凭超时消息不能区分这两者**：
实际项目应结合波形或 DUT 日志判断是否最终输出。只有规格允许更晚完成，才应该修改预算；
否则加大预算会掩盖迟到问题。

## 练习四：第一笔已经正确，后来又多回一笔

```bash
python3 -m pytest -q examples/getting_started/failure_lab.py -k extra --tb=short
```

预期 `ScoreboardAssociationError`，消息含 `1 unmatched observation(s)`。
第一笔响应是正确的，额外响应发生在下一拍；它被 `observe_cycles=2` 的结束观察捕获。
这正是不能只等待“最后一个正确响应”就宣布成功的原因。

## 看到错误后，从哪里改起

| 现象 | 首先确认 | 不应直接得出的结论 |
| --- | --- | --- |
| AssertionError / ScoreboardMismatch | 规格、期望值、输入编码、采样快照 | 一定是 DUT 算错 |
| ScoreboardTimeoutError | 输入何时接受，响应预算，输出采样条件 | 只要加大超时就对了 |
| ScoreboardAssociationError | 是否多回、重复回，key 是否对应，空闲值是否误采 | 丢弃多出来的样本即可 |
| 退出时有未完成事务 | 是否调用 finish、请求是否在预算内完成 | 关闭就应当自动算通过 |

想一次看到四种错误，可以去掉 `-k ...` 运行同一个文件，预期 `4 failed`。
正常回归使用 `python3 -m pytest -q examples/getting_started`，应全部通过。

[下一步：通过的测试到底覆盖了哪些情况](coverage.md)。
