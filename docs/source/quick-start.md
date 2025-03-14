# 快速开始

本节将介绍如何使用 Toffee 框架完成一个简单的验证任务。我们将通过一个加法器设计的验证过程，演示 Toffee 的基本使用方法。该过程包括将加法器设计转换为 Python 包、编写驱动方法、创建验证模型、编写测试用例以及添加功能检查点等步骤。

在开始之前，您需要安装 Toffee 框架的基础工具 Picker。您可以在 [这里](https://github.com/XS-MLVP/picker) 找到 Picker 的安装方法。接着，您可以通过以下命令安装 Toffee 及其测试工具 `toffee-test`：

```bash
pip install pytoffee toffee-test
```

## 生成加法器设计

我们将使用一个简单的加法器示例来演示 Toffee 的使用方法。设计代码如下：

```verilog
// example/adder/adder.v
module Adder #(
    parameter WIDTH = 64
) (
    input  [WIDTH-1:0] io_a,
    input  [WIDTH-1:0] io_b,
    input              io_cin,
    output [WIDTH-1:0] io_sum,
    output             io_cout
);

assign {io_cout, io_sum}  = io_a + io_b + io_cin;

endmodule
```

执行以下命令将 `adder.v` 转换为可供 Toffee 使用的 Python 包，或者直接在 `example/adder` 目录下执行 `make dut`：

```bash
picker export --autobuild=true adder.v -w Adder.fst --sname Adder --tdir picker_out_adder --lang python -e -c --sim verilator
```

该命令将生成 `picker_out_adder` 目录，您可以通过 `from picker_out_adder import DUTAdder` 导入可直接运行的加法器设计。接下来，我们将通过 Toffee 框架对该加法器的功能进行验证。

## 创建 Bundle 及 Agent

Toffee 使用 `Bundle` 来描述需要驱动的接口，并使用 `Agent` 来编写对该接口的驱动方法。在以下代码中，我们创建了一个 `AdderBundle` 用于描述加法器的接口，以及一个 `AdderAgent` 用于编写对该接口的驱动方法：

```python
# test_adder.py

from toffee import *

class AdderBundle(Bundle):
    a, b, cin, sum, cout = Signals(5)

class AdderAgent(Agent):
    @driver_method()
    async def exec_add(self, a, b, cin):
        self.bundle.a.value = a
        self.bundle.b.value = b
        self.bundle.cin.value = cin
        await self.bundle.step()
        return self.bundle.sum.value, self.bundle.cout.value
```

使用 `Bundle` 的意义在于，我们可以在验证代码中预先定义好需要驱动的接口信号结构，而不需要关心 DUT 具体的接口信号名称。通过 `Bundle` 提供的映射方法，可以将信号映射至任意具有相同结构的 DUT 接口信号上，从而实现验证代码与 DUT 的解耦。

接下来，我们使用 `driver_method` 装饰器来标记 `Agent` 中的驱动方法 `exec_add`。该方法完成了对加法器的一次驱动操作。每当该方法被调用时，它会将输入信号 `a`、`b`、`cin` 的值分别赋给加法器的输入端口，并在下一个时钟周期后读取加法器的输出信号 `sum` 和 `cout` 的值并返回。在 `Agent` 中还可以定义**监测方法(monitor_method)**，该方法会在后台持续监测 DUT 的输出信号，具体请参考 [如何编写 Agent](env/agent.md)。

您可以将上述代码作为一个小型的验证封装，并在测试用例中调用该驱动方法来驱动加法器。

```python
# test_adder.py (continued)
import toffee_test
from picker_out_adder import DUTAdder

@toffee_test.testcase
async def test_adder():
    adder = DUTAdder()                                        # 实例化加法器
    start_clock(adder)                                        # 启动 toffee 内置时钟
    adder_bundle = AdderBundle.from_prefix("io_").bind(adder) # 利用前缀映射方法将 Bundle 与 DUT 进行绑定
    adder_agent = AdderAgent(adder_bundle)                    # 实例化 Agent
    sum, cout = await adder_agent.exec_add(1, 2, 0)           # 调用驱动方法
    assert sum == 3 and cout == 0                             # 验证输出结果
```

在当前目录运行 `pytest` 命令，您将会看到这个简易的测试用例运行成功。然而，加法器的结果仍需人工比对验证。为了使测试更加自动化，接下来我们将利用 Toffee 提供的 `Model` 类来自动验证加法器的输出结果。

## 使用 Model 进行自动化检验

在此之前，我们需要使用 `Env` 来打包整个验证环境。`Env` 是 Toffee 中的顶层环境类，用于管理所有的 `Agent` 和 `Model`。由于加法器的验证环境中只有一个 `Agent`，因此我们只需在 `Env` 中实例化这个 `Agent` 即可。

```python
# test_adder.py (continued)

class AdderEnv(Env):
    def __init__(self, adder_bundle):
        super().__init__()
        self.add_agent = AdderAgent(adder_bundle)
```

`AdderEnv` 创建完成后，整个验证环境的结构也随之确定。此后，测试用例中将无需考虑 DUT 中的硬件接口，而是通过调用该层级结构中的软件接口来驱动 DUT：

```text
AdderEnv
  - add_agent
    - exec_add
```

为了编写加法器的参考模型，我们在 `Model` 类中创建一个 `driver_hook` 方法，如下所示：

```python
# test_adder.py (continued)

class AdderModel(Model):
    @driver_hook(agent_name="add_agent")
    def exec_add(self, a, b, cin):
        result = a + b + cin
        sum = result & ((1 << 64) - 1)
        cout = result >> 64
        return sum, cout
```

该方法用于截取 `add_agent` 中 `exec_add` 方法的调用。在定义该方法时，我们使其与 `Agent` 中的 `exec_add` 方法具有相同的输入参数，并返回加法器的标准返回值。此后，每当 `add_agent` 中的 `exec_add` 方法被调用时，`Model` 中的 `exec_add` 方法将会被自动触发，并将二者的返回值进行自动比对，从而实现对加法器的自动验证。

## 使用 toffee-test 管理测试用例

在此之后，我们使用 `toffee-test` 提供的方法来管理测试用例。我们将创建一个 `adder_env` 的 Fixture，用于在每个测试用例之前初始化验证环境。在该 Fixture 中，我们使用了 `toffee_request.create_dut` 方法来创建加法器实例，从而 DUT 的波形、覆盖率文件及测试报告会由 `toffee-test` 生成并收集到指定文件夹。*注意，当初始化含有时钟 `clock` 信号接口的 DUT 时，您需要将 DUT 的时钟接口名称传入 `toffee_request.create_dut` 方法中，以便 `toffee-test` 正确识别时钟信号。*

```python
# test_adder.py (continued)

@toffee_test.fixture
async def adder_env(toffee_request: toffee_test.ToffeeRequest):
    dut = toffee_request.create_dut(DUTAdder)
    start_clock(dut)
    return AdderEnv(AdderBundle.from_prefix("io_").bind(dut))
```

此后，在测试用例参数中使用 `adder_env` Fixture 即可在测试用例中使用验证环境。以下是为加法器编写的两个测试用例：

```python
# test_adder.py (continued)

import random

@toffee_test.testcase
async def test_random(adder_env):
    for _ in range(1000):
        a = random.randint(0, 2**64 - 1)
        b = random.randint(0, 2**64 - 1)
        cin = random.randint(0, 1)
        await adder_env.add_agent.exec_add(a, b, cin)

@toffee_test.testcase
async def test_boundary(adder_env):
    for cin in [0, 1]:
        for a in [0, 2**64 - 1]:
            for b in [0, 2**64 - 1]:
                await adder_env.add_agent.exec_add(a, b, cin)
```

在当前目录运行 `pytest --toffee-report` 命令，您将会看到测试用例运行成功，并且 `toffee-test` 会自动收集测试用例的执行结果，自动统计覆盖率信息，并在 `reports` 目录下生成验证报告。您也可以直接在 `example/adder` 目录下运行 `make run` 来查看测试结果。

## 添加功能检查点

**功能检查点(Cover Point)** 在验证中用于检验待测设计的某种情况是否被验证到。同一类功能检查点可以被组织成一个**测试组(Cover Group)**，用于统计某一类功能检查点的覆盖率。在 Toffee 中，我们使用 `CovGroup` 和 `add_watch_point` 来定义测试组并添加功能检查点。例如，以下代码为加法器创建了一个测试组：

```python
# test_adder.py (continued)

import toffee.funcov as fc
from toffee.funcov import CovGroup

def adder_cover_point(adder):
    g = CovGroup("Adder addition function")

    g.add_cover_point(adder.io_cout, {"io_cout is 0": fc.Eq(0)}, name="Cout is 0")
    g.add_cover_point(adder.io_cout, {"io_cout is 1": fc.Eq(1)}, name="Cout is 1")
    g.add_cover_point(adder.io_cin, {"io_cin is 0": fc.Eq(0)}, name="Cin is 0")
    g.add_cover_point(adder.io_cin, {"io_cin is 1": fc.Eq(1)}, name="Cin is 1")
    g.add_cover_point(adder.io_a, {"a > 0": fc.Gt(0)}, name="signal a set")
    g.add_cover_point(adder.io_b, {"b > 0": fc.Gt(0)}, name="signal b set")
    g.add_cover_point(adder.io_sum, {"sum > 0": fc.Gt(0)}, name="signal sum set")

    return g
```

该测试组检验了加法器的 `io_cout` 和 `io_cin` 的取值是否全部覆盖到，并且检验了 `io_a`、`io_b` 和 `io_sum` 的取值是否大于 0。我们可以在此前创建的 Fixture 中将该测试组添加到验证环境中：

```python
# test_adder.py (modified)

@toffee_test.fixture
async def adder_env(toffee_request: toffee_test.ToffeeRequest):
    dut = toffee_request.create_dut(DUTAdder)
    toffee_request.add_cov_groups(adder_cover_point(dut))  # 添加测试组
    start_clock(dut)
    return AdderEnv(AdderBundle.from_prefix("io_").bind(dut))
```

此时，再次运行 `pytest --toffee-report` 或在 `example/adder` 目录下运行 `make run`，您将会看到测试报告中包含了功能覆盖率信息。关于功能检查点的更多信息，请参考 [功能检查点](cases/cov.md)。

---

本节介绍了使用 Toffee 框架完成一个简单的验证任务的流程。继续阅读后续章节以了解更多 Toffee 的功能和用法。
