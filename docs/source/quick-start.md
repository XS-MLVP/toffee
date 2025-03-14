# 快速开始

## 安装

### toffee

[Toffee](https://github.com/XS-MLVP/toffee) 是一款基于 Python 的硬件验证框架，旨在帮助用户更加便捷、规范地使用 Python 构建硬件验证环境。它依托于多语言转换工具 [picker](https://github.com/XS-MLVP/picker)，该工具能够将硬件设计的 Verilog 代码转换为 Python Package，使得用户可以使用 Python 来驱动并验证硬件设计。

Toffee 需要的依赖有：

- Python 3.6.8+
- Picker 0.9.0+

当安装好上述依赖后,可通过pip安装toffee：

```bash
pip install pytoffee
```

或通过以下命令安装最新版本的toffee：

```bash
pip install pytoffee@git+https://github.com/XS-MLVP/toffee@master
```

或通过以下方式进行本地安装：

```bash
git clone https://github.com/XS-MLVP/toffee.git
cd toffee
pip install .
```
### toffee-test

[Toffee-test](https://github.com/XS-MLVP/toffee-test/tree/master) 是一个用于为 Toffee 框架提供测试支持的 Pytest 插件，他为 toffee 框架提供了将测试用例函数标识为 toffee 的测试用例对象，使其可以被 toffee 框架识别并执行;测试用例资源的管理功能;测试报告生成功能，以便于用户编写测试用例。

通过 pip 安装 toffee-test

```bash
pip install toffee-test
```

或安装开发版本

```bash
pip install toffee-test@git+https://github.com/XS-MLVP/toffee-test@master
```

或通过源码安装

```bash
git clone https://github.com/XS-MLVP/toffee-test.git
cd toffee-test
pip install .
```


## 搭建简单的验证环境

我们使用一个简单的加法器示例来演示 toffee 的使用方法，该示例位于 `example/adder` 目录下。

加法器的设计如下：

```verilog
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

首先使用 picker 将其转换为 Python Package，再使用 toffee 来为其建立验证环境。安装好依赖后，可以直接在 `example/adder` 目录下运行以下命令来完成转换：

```bash
make dut
```

为了验证加法器的功能，我们使用 toffee 提供的方法来建立验证环境。

首先需要为其创建加法器接口的驱动方法，这里用到了 `Bundle` 来描述需要驱动的某类接口，`Agent` 用于编写对该接口的驱动方法。如下所示：

```python
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

我们使用了 `driver_method` 装饰器来标记 `Agent` 中用于驱动的方法 `exec_add`，该方法完成了对加法器的一次驱动操作，每当该方法被调用，其会将输入信号 `a`、`b`、`cin` 的值分别赋给加法器的输入端口，并在下一个时钟周期后读取加法器的输出信号 `sum` 和 `cout` 的值并返回。

`Bundle` 是该 `Agent` 需要驱动的接口的描述。在 `Bundle` 中提供了一系列的连接方法来连接到 DUT 的输入输出端口。这样一来，我们可以通过此 `Agent` 完成所有拥有相同接口的 DUT 的驱动操作。

为了验证加法器的功能，我们还需要为其创建一个参考模型，用于验证加法器的输出是否正确。在 toffee 中，我们使用 `Model` 来定义参考模型。如下所示：

```python
class AdderModel(Model):
    @driver_hook(agent_name="add_agent")
    def exec_add(self, a, b, cin):
        result = a + b + cin
        sum = result & ((1 << 64) - 1)
        cout = result >> 64
        return sum, cout
```

在参考模型中，我们同样定义了一个 `exec_add` 方法，该方法与 `Agent` 中的 `exec_add` 方法含有相同的输入参数，我们用程序代码计算出了加法器的标准返回值。我们使用了 `driver_hook` 装饰器来标记该方法，以便该方法可以与 `Agent` 中的 `exec_add` 方法进行关联。

接下来，我们需要创建一个顶层的测试环境，将上述的驱动方法与参考模型相关联，如下所示：

```python
class AdderEnv(Env):
    def __init__(self, adder_bundle):
        super().__init__()
        self.add_agent = AdderAgent(adder_bundle)

        self.attach(AdderModel())
```

此时，验证环境已经搭建完成，toffee 会自动驱动参考模型并收集结果，并将结果与加法器的输出进行比对。

之后，需要编写测试用例来验证加法器的功能，通过 toffee-test，可以使用如下方式编写测试用例。

```python
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

可以直接在 `example/adder` 目录下运行以下命令来运行该示例：

```bash
make run
```

运行结束后报告将自动在`reports`目录下生成。
