# 用类定义覆盖模型

小模型可以直接使用 `CoverGroupDef`，见[功能覆盖率](coverage.md)。
模型较大、多个模块需要复用分桶规则时，可以用 `xreactor.declarative` 中的类声明。
两种写法使用相同的计数、合并和报告机制，不需要换一套运行器。

## 样本、覆盖点和覆盖组

样本保存一次实际观察到的数据；覆盖点把数据分成不同情况，每一种称为一个 bin；
覆盖组把相关覆盖点放在一起，也可以统计它们的交叉组合。

下面的代码可以直接运行：

```python
from dataclasses import dataclass
from xreactor.declarative import Bin, CoverGroup, CoverPoint, Cross, covergroup, coverpoint


@dataclass(frozen=True)
class Sample:
    state: int
    taken: bool


@coverpoint
class StatePoint(CoverPoint[int]):
    idle = Bin.values(0)
    busy = Bin.values(1, at_least=2)
    done = Bin.values(2)
    path = Bin.transition(0, 1, 2)


@coverpoint
class TakenPoint(CoverPoint[bool]):
    yes = Bin.values(True)
    no = Bin.values(False)


@covergroup(schema_id="pipeline.activity", contract="observed-pipeline/v1")
class PipelineCoverage(CoverGroup[Sample]):
    state = StatePoint()
    taken = TakenPoint()
    state_taken = Cross(state, taken)


coverage = PipelineCoverage(instance="pipeline0")
coverage.sample(Sample(state=0, taken=False))
coverage.sample(Sample(state=1, taken=True))
coverage.sample(Sample(state=2, taken=True))

assert coverage.state.count(StatePoint.busy) == 1
assert coverage.state.count(StatePoint.path) == 1
```

`busy` 要命中两次才达到目标，上面只命中一次，因此整个模型还没有完成覆盖。
`path` 检查相邻三次采样是否依次为 0、1、2，不是检查单次数据里是否含有这三个值。

## 样本从哪里来

手动 `sample()` 适合已经由 Monitor 整理好的快照或完成事务。
样本应来自实际观察，而不是因为测试“打算测忙碌”就直接填入 `state=1`。

默认按同名字段取值：`state` 读 `Sample.state`。需要取别的字段或嵌套字段时，
用 `Fields(Sample).select(lambda s: s.state)` 显式指定。
整个样本都会检查类型，`int` 字段不能用 `bool` 代替。

## 自动从信号采样

如果已有绑定好的 `dut.state`、`dut.taken` 和 `clock`，可以这样接线：

```python
from xreactor import RisingEdge
from xreactor.declarative import Fields, wire

fields = Fields(Sample)
coverage.bind(
    trigger=RisingEdge(clock),
    fields=(
        wire(fields.select(lambda s: s.state), dut.state, source_id="dut.state"),
        wire(fields.select(lambda s: s.taken), dut.taken, source_id="dut.taken"),
    ),
    strategy="native",
)
```

将 `coverage.runtime` 放入 `Execution(backend, coverage=[coverage.runtime])`，
进入执行环境后便会自动采样。`source_id` 标明字段来自哪个信号，便于核对绑定和合并报告。
自动信号采样支持 `int` 和 `bool` 字段，其中 `bool` 必须绑定一位信号。

`strategy="native"` 要求原生引擎执行，不支持的绑定会报错。
选择 `"auto"` 时允许整个组改用 Python，并在报告中说明原因。

## 复用模型和检查定义

覆盖点可以通过子类增加 bin，覆盖组可以用这个子类替换原来的覆盖点。
已有实例和基类不会跟着改变；继承的 Cross 则按新模型重新展开。
多个独立定义也可以用 `CoverageFragment[Sample]` 组合。

模型编译后不要再修改类属性或样本注解。需要新定义时，创建新类或子类。
`PipelineCoverage.compile().explain()` 列出字段、bin、目标次数和交叉组合；
`diff()` 可以比较两个编译后的模型。定义或采样规则改变后，旧报告可能不能合并。

## 运行完整示例

在仓库根目录执行：

```bash
python3 -m examples.coverage.declarative_pipeline --backend manual
python3 -m examples.coverage.declarative_pipeline --backend native --output-dir output/classes
```

源码见 [declarative_pipeline.py](https://github.com/Makiras/xreactor/blob/main/examples/coverage/declarative_pipeline.py)。
按请求编号统计延迟和乱序，见[事务覆盖](coverage-transactions.md)；
直接识别跨周期条件，见[时序覆盖](coverage-patterns.md)。
