# Toffee

[![PyPI version](https://badge.fury.io/py/pytoffee.svg)](https://badge.fury.io/py/pytoffee)

[English Version](README.md) | [中文版本](README_zh.md)

Toffee is a Python-based hardware verification framework designed to help users build hardware verification environments more conveniently and systematically using Python. It leverages the multi-language conversion tool [Picker](https://github.com/XS-MLVP/picker), which converts Verilog code of hardware designs into Python Packages, enabling users to drive and verify hardware designs in Python.

Toffee incorporates elements of UVM methodology to ensure the verification environment's standardization and reusability. Moreover, Toffee redesigns the verification environment setup to better align with software developers' workflows, making it easier for software developers to engage in hardware verification tasks.

For more information about Toffee, please refer to the [Toffee Documentation](https://pytoffee.readthedocs.io/zh-cn/latest/).

## Installation

Toffee requires the following dependencies:

- Python 3.8.0+
- Picker 0.9.0+

Once these dependencies are installed, you can install Toffee via pip:

```bash
pip install pytoffee
```

Or install the latest version of Toffee with the following command:

```bash
pip install pytoffee@git+https://github.com/XS-MLVP/toffee@master
```

For a local installation:

```bash
git clone https://github.com/XS-MLVP/toffee.git
cd toffee
pip install .
```

## Usage

We use a simple adder example located in the `example/adder` directory to demonstrate how to use Toffee.

The design of the adder is as follows:

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

Before building the verification environment using Toffee, you need to use picker to convert the design into a Python Package. After installing the dependencies, run the following command in the `example/adder` directory to complete the conversion:

```bash
make dut
```

To verify the functionality of the adder, you need to set up the verification environment using Toffee.

First, create a driver method for the adder interface. Here, `Bundle` describes the interface to be driven, while `Agent` is used to write the driving method for this interface. The setup is shown below:

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

To verify the functionality of the adder, define a `Model` class to capture interaction information with the DUT and perform comparisons.

```python
class AdderModel(Model):
    @driver_hook(agent_name="add_agent")
    def exec_add(self, a, b, cin):
        result = a + b + cin
        sum = result & ((1 << 64) - 1)
        cout = result >> 64
        return sum, cout
```

Next, create a top-level test environment and associate it with the Model, as shown below:

```python
class AdderEnv(Env):
    def __init__(self, adder_bundle):
        super().__init__()
        self.add_agent = AdderAgent(adder_bundle)

        self.attach(AdderModel())
```

At this point, the verification environment is complete. The methods in the Model will be automatically invoked and compared with the output of the adder.

Afterward, write test cases to verify the functionality of the adder. With [toffee-test](https://github.com/XS-MLVP/toffee-test/tree/master), test cases can be written as follows:

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

Run the example in the `example/adder` directory with the following command:

```bash
make run
```

A report will be automatically generated in the `reports` directory upon completion.

For more detailed usage, please refer to the [Toffee Documentation](https://pytoffee.readthedocs.io/zh-cn/latest/).

## Additional Information

This project is part of **UnityChip's** open verification initiative. For more information, please visit [open-verify.cc](https://open-verify.cc).
