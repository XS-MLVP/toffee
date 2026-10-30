# 从零开始的累加器教程

[学习路线](../../docs/zh/getting-started/index.md) 配合一个持续扩展的主线文件阅读。
命令均在仓库根目录执行，先安装 `python3 -m pip install -e '.[test]'`。

## 主线只看两个文件

| 文件 | 用途 |
| --- | --- |
| `test_accumulator.py` | 按章节顺序排列测试、Driver、Monitor、Agent 和参考模型；新定义就在使用它的例子旁边 |
| `toy_dut.py` | 已提供的教学累加器替身；端口和接受规则见教程第 2 步，无需先研究后端实现 |

仓库已经提供完整主线；直接定位当前章节的段落即可。如果从空文件跟写，按文档顺序
追加每段代码，保留前面的 import 和定义。后面的章节会复用它们。

只运行第一步：

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_addition
```

其他章节换成对应函数名：

| 章节 | 测试函数 |
| --- | --- |
| 输入与空闲 | `test_one_input_and_idle` |
| 多组数据与边界 | `test_accumulator_cases`（五组） |
| Driver | `test_driver_restores_idle` |
| Monitor 快照 | `test_delayed_response_snapshot` |
| Agent | `test_agent_checks_responses` |
| 模型与 fixture | `test_model_checks_a_sequence`、`test_model_checks_wraparound` |
| 连续提交 | `test_three_inputs_in_flight` |
| 多批次和撤销待发输入 | `test_two_batches_and_withdrawn_input` |
| 覆盖率 | `test_result_coverage`（加 `-s` 可查看报告路径） |

完整主线是 15 项通过用例，不要求 xspcomm 或 RTL：

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py
```

`toy_dut.py` 是教学替身，不是 RTL；一拍使能、8 位累加等规则只属于这个例子。

## 按需打开的四个文件

| 文件 | 用途 |
| --- | --- |
| `failure_lab.py` | 四种故意失败的练习，显式运行 |
| `test_tutorial_out_of_order.py` | 带 tag 的乱序累加器变体 |
| `native_lab.py` | 主线 Agent 换用 native 时钟，显式运行 |
| `check_native.py` | 独立检查 binding 安装路径与时钟推进 |

故障和 native 练习直接复用主线中已讲解的模型和 Agent 工厂。故障例子例如：

```bash
python3 -m pytest -q examples/getting_started/failure_lab.py -k wrong --tb=short
```

这条命令应失败。其他故障用 `-k missing`、`-k late`、`-k extra`。它们不会被默认目录测试发现。
配置好 xspcomm 后，native 选读的命令是：

```bash
python3 examples/getting_started/check_native.py
python3 -m pytest -q examples/getting_started/native_lab.py
```

运行整个目录会执行主线和乱序例子，共 16 项正常用例：

```bash
python3 -m pytest -q examples/getting_started
```
