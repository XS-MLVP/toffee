# 1. 第一次 PASS 和 FAIL

目标：知道测试怎样判定结果，失败时应当看哪里。这一步不引入时钟或验证组件。

## 一个只有四行的测试

打开 `examples/getting_started/test_accumulator.py`，从文件最前面的 `test_addition` 看起。
仓库已放好后续所有步骤，现在只需理解下面四行：

<!-- executable-example: examples/getting_started/test_accumulator.py#assertion -->
```python
def test_addition():
    expected = 8
    actual = 3 + 5
    assert actual == expected
```

`def` 定义函数。函数名以 `test_` 开头，pytest 就会把它当成测试。
缩进的三行属于这个函数；统一使用四个空格。`=` 保存一个值，`==` 比较两个值。
`assert` 表示“这里必须成立”，不成立就使这个测试失败。

这里我们先用 `3 + 5` 代替待检查的计算；下一步才把 `actual` 换成从 DUT 读出的结果。

在**终端**执行：

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_addition
```

应看到类似输出（运行时间会不同）：

```text
.                                                                        [100%]
1 passed in ...
```

`.` 表示一个测试通过，`-q` 让输出简短。命令末尾的 `::test_addition` 只运行这个函数，
不会把后续章节的用例一起执行。这里要用 pytest 运行测试，直接用 Python 打开文件不会自动调用测试函数。

## 故意写错一次

把 `expected = 8` 改成 `expected = 9`，重新执行上面的命令。关键输出会变为：

```text
E       assert 8 == 9
1 failed in ...
```

它告诉你实际算出了 8，但测试期待 9。先检查规格和测试数据，再判断是测试写错还是 DUT
出错；看到 FAIL 并不自动意味着硬件有 bug。现在把期望值改回 8，确认再次通过。

## 自己加一项检查

在同一个文件最后追加：

```python
def test_eight_bit_wraparound():
    actual = (250 + 10) % 256
    assert actual == 4
```

`%` 是取余数；8 位无符号数能保存 0～255，260 取低 8 位得到 4。
用函数名运行新增的检查：

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_eight_bit_wraparound
```

应看到 `1 passed`。这项练习还没有访问 DUT，只是在理解后续要验证的规格。

接下来不需要把练习代码复制到其他文件。
[下一步：给 DUT 输入，等待一个时钟周期](clock-and-input.md)。
