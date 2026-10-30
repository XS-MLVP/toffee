# 10. 知道哪些情况已经测到

目标：把“所有运行过的比较都正确”和“计划中的场景都出现过”分开检查。
只测了 3、5、7 也能全部通过，但尚未碰到结果为 0 或 255 的情况。

## 先定三个观察目标

本页按**实际检查成功的响应**分成三类，称为三个 bin（覆盖目标）：

| bin | 结果范围 | 本例怎样触发 |
| --- | --- | --- |
| zero | 0 | 初值 0，加 0 |
| maximum | 255 | 再加 255 |
| middle | 1～254 | 再加 2，溢出得到 1 |

这里只定义了三个结果类别。三个都命中叫这份覆盖计划达到 100%，并不意味着所有输入组合、
所有时序或所有设计行为都验证完毕。规格增加其他要求时，要增加相应测试和覆盖目标。

## 本节测试和报告保存

下面是 `examples/getting_started/test_accumulator.py` 中本节新增的部分；前面章节的定义继续保留：

<!-- executable-example: examples/getting_started/test_accumulator.py#coverage -->
```python
from toffee import Bin, CoverGroupDef, CoverPointDef, CoverageDatabase


@pytest.mark.asyncio
async def test_result_coverage(tmp_path):
    definition = CoverGroupDef("accumulator", (
        CoverPointDef("result", {
            "zero": Bin.values(0),
            "middle": Bin.range(1, 254),
            "maximum": Bin.values(255),
        }),
    ))
    coverage = definition.instantiate("tutorial")
    dut = TutorialDut()
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            for operand in [0, 255, 2]:
                transfer = agent.submit(operand)
                result = await transfer
                coverage.sample({"result": result})
            await agent.finish(timeout_cycles=10, observe_cycles=1)
        coverage.assert_coverage(100)
        print(f"Result bins: {coverage.coverage:.0f}%")
    finally:
        report = tmp_path / "functional.json"
        try:
            CoverageDatabase([coverage]).write_json(report)
            print(f"Coverage JSON: {report}")
        finally:
            dut.close()
```

```bash
python3 -m pytest -q -s examples/getting_started/test_accumulator.py::test_result_coverage
```

应看到：

```text
Result bins: 100%
Coverage JSON: /.../test_result_coverage0/functional.json
...
1 passed in ...
```

`-s` 让 `print()` 的输出直接显示。`tmp_path` 是 pytest 自带的临时目录 fixture，不需要
自己定义；`tmp_path / "functional.json"` 表示在该目录里选一个文件路径。

`CoverGroupDef` 定义这一组目标，`CoverPointDef("result", ...)` 定义要看的字段和分类，
`instantiate("tutorial")` 创建本次运行的计数器。`sample({"result": result})` 记录一次命中。
这里先 `await transfer` 得到已检查的响应，再计数；没有直接把刚提交的输入当成已完成场景。

这个例子逐笔提交并等待响应，目的是清楚展示一次响应对应一次覆盖采样。要测试吞吐量，
使用第 8 步的先提交后等待方式。本例不会再去读取 Agent 的响应 Monitor，避免抢走检查器的输入。

报告写在 `finally` 中：即使比较或覆盖目标失败，也尽量保留已经采到的计数；DUT 随后关闭。

## 故意漏掉一类

把输入列表 `[0, 255, 2]` 改为 `[0, 255]`。结果比较仍正确，但最后的
`coverage.assert_coverage(100)` 会失败，报告约 66.67%，并指出 `middle` 未覆盖。
恢复列表后应重新达到 100%。

## 把 JSON 转成可阅读的 HTML

把上一条命令实际打印出的 JSON 路径复制到下面引号中；这里的路径是占位符，需要替换：

```bash
toffee-coverage-report --functional '/实际打印的路径/functional.json' --output tutorial-coverage.html
```

在浏览器打开仓库根目录的 `tutorial-coverage.html`，查看三个 bin 的命中数。
这份功能覆盖率不需要 RTL 行覆盖率工具。更复杂的 bin、cross、多次运行合并，见
[覆盖率指南](../guides/coverage.md)。pytest 的通过/失败列表和功能覆盖率回答不同问题，
多个 case 可以共同贡献覆盖，见[功能点与跨用例覆盖](../guides/functional-points.md)。

主线到这里已经能完成输入、采样、模型检查、结束检查和报告保存。
可以选读 [乱序响应](out-of-order.md)，或 [换成 native 时钟和项目 DUT](native-and-next.md)。
