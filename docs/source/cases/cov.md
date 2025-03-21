# 功能检查点（功能覆盖率）

## 什么是功能检查点

在 toffee 中，**功能检查点(Cover Point)** 是指对设计的某个功能进行验证的最小单元，判断该功能是否满足设计目标。**测试组(Cover Croup)** 是一类检查点的集合。

定义一个检查点，需要指定检查点的名称及检查点的触发条件（触发条件可以有多个，最终的检查结果为所有条件取“逻辑与”，触发条件称为`Cover Bin`）。例如，可以定义了一个检查点，“当加法器运算结果不为 0 时，结果运算正确”，此时，检查点的触发条件可以为 “加法器的 sum 信号不为零”。

当检查点的所有触发条件都满足时，检查点被触发，此时，验证报告将会记录下该检查点的触发。并会提升验证的功能覆盖率。当所有检查点都被触发时，验证的功能覆盖率达到 100%。

## 如何编写检查点

编写检查点前，首先需要创建一个测试组，并指定测试组的名称

```python
import toffee.funcov as fc

g = fc.CovGroup("Group-A")
```

接着，需要往这个测试组中添加检查点。一般情况下，一个功能点对应一个或多个检查点，用来检查是否满足该功能。例如我们需要检查`Adder`的`cout`是否有`0`出现，我们可以通过如下方式添加：

```python
g.add_watch_point(adder.io_cout,
                  {"io_cout is 0": fc.Eq(0)},
                  name="cover_point_1")
```

在上述检查点中，需要观察的数据为`io_cout`引脚，检查条件(`Cover Bin`)的名称为`io_cout is 0`，检查点名称为`cover_point_1`。函数`add_watch_point`的参数说明如下：

```python
def add_watch_point(target,
                    bins: dict,
                    name: str = "", once=None):
        """
        @param target: 检查目标，可以是一个引脚，也可以是一个DUT对象
        @param bins: 检查条件，dict格式，key为条件名称，value为具体检查方法或者检查方法的数组。
        @param name: 检查点名称
        @param once，如果once=True，表明只检查一次，一旦该检查点满足要求后就不再进行重复条件判断。
```

通常情况下，`target`为`DUT`引脚，`bins`中的检查函数来检查`target`的`value`是否满足预定义条件。`funcov`模块内存了部分检查函数，例如`Eq(x), Gt(x), Lt(x), Ge(x), Le(x), Ne(x), In(list), NotIn(list), isInRange([low,high])`等。当内置检查函数不满足要求时，也可以自定义，例如需要跨时钟周期进行检查等。自定义检查函数的输入参数为`target`，返回值为`bool`。例如：

```python
g.add_watch_point(adder.io_cout,
                  {
                    "io_cout is 0": lambda x: x.value == 0,
                    "io_cout is 1": lambda x: x.value == 1,
                    "io_cout is x": fc.Eq(0),
                    # 注：zx态，vcs后端支持，verilator不支持
                  },
                  name="cover_point_1")
```

当添加完所有的检查点后，需要在`DUT`的`Step`回调函数中调用`CovGroup`的`sample()`方法进行判断。在检查过程中，或者测试运行完后，可以通过`CovGroup`的`as_dict()`方法查看检查情况。

```python
dut.StepRis(lambda x: g.sample())

...

print(g.as_dict())
```

## 如何在测报告中展示

在测试`case`每次运行结束时，可以通过`set_func_coverage(request, cov_groups)`告诉框架对所有的功能覆盖情况进行合并收集。相同名字的`CoverGroup`会被自动合并。下面是一个简单的例子：

```python
import pytest
import toffee.funcov as fc
from toffee_test.reporter import set_func_coverage

g = fc.CovGroup("Group X")

def init_function_coverage(g):
    # add your points here
    pass

@pytest.fixture()
def dut_input(request):
    # before test
    init_function_coverage(g)
    dut = DUT()
    dut.InitClock("clock")
    dut.StepRis(lambda x: g.sample())
    yield dut
    # after test
    dut.Finish()
    set_func_coverage(request, g)
    g.clear()

def test_case1(dut_input):
    assert True

def test_case2(dut_input):
    assert True

# ...
```

在上述例子中，每个`case`都会通过`dut_input`函数来创建输入参数。该函数用`yield`返回`dut`，在运行`case`前初始化`dut`，并且设置在`dut`的`step`回调中执行`g.sample()`。运行完`case`后，调用`set_func_coverage`收集覆盖率，然后清空收集的信息。所有测试运行完成后，可在生成的测试报告中查看具体的覆盖情况。
