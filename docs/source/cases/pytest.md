# 如何使用 Pytest 管理测试用例

在 toffee 中，测试用例是通过 pytest 来管理的。pytest 是一个功能强大的 Python 测试框架，如果你不熟悉 pytest，可以查看 [pytest 官方文档](https://docs.pytest.org/en/latest/)。`toffee-test` 是 toffee 提供的一个 pytest 插件，用于管理 toffee 测试用例。

## 编写第一个测试用例

首先，我们需要创建一个测试用例文件，例如 `test_adder.py`，该文件需要以 `test_` 开头，或以 `_test.py` 结尾，以便 pytest 能够识别。接着可以在其中编写我们的第一个测试用例。对于 toffee 测试用例，我们需要在测试用例中导入 `toffee_test` 模块，并使用 `@toffee_test.testcase` 装饰器来标记测试用例，例如：

```python
# test_adder.py

import toffee_test

@toffee_test.testcase
async def my_test():
    adder = DUTAdder(
        waveform_filename="adder.fst",
        coverage_filename="adder.dat"
    )
    # ...
```

用例编写完成后，我们可以在终端中运行 pytest。

```bash
pytest
```

pytest 会查找当前目录下所有以 `test_` 开头或以 `_test.py` 结尾的文件，并运行其中以 `test_` 开头的函数，每一个函数被视作一个测试用例。


## 利用 Fixture 管理资源

然而，使用上述方法编写测试用例时，我们需要在每个用例中手动管理资源的创建与释放，例如波形文件和覆盖率文件的生成。并且还需要保证每个测试用例之间文件名称不产生冲突，这要求我们在每个测试用例中传入不一样的文件名称。并且在测试用例出现异常时，测试用例并不会运行完毕，导致覆盖率文件无法生成。

因此，toffee-test 提供了 `toffee_request` Fixture 来管理资源，简化了测试用例的编写。利用 `toffee_request` Fixture，我们可以将资源的创建与释放工作交给 Fixture 来完成，测试用例只需要关注测试逻辑即可。使用方式如下：

```python
# test_adder.py

import toffee_test

@toffee_test.testcase
async def test_adder(adder_env):
    pass

@toffee_test.fixture
async def adder_env(toffee_request: ToffeeRequest):
    dut = toffee_request.create_dut(DUTAdder) # 如果有时钟引脚，需要额外传入时钟引脚名称，例如 "clk"
    toffee_request.add_cov_groups(CovGroup("Adder"))
    toffee.start_clock(dut)
    return AdderEnv(AdderBundle.from_prefix("io_").bind(dut))

```

Fixture 是 pytest 中的概念，例如上述代码中定义了一个名为 `adder_env` 的 Fixture。如果在其他测试用例的输出参数中含有 `adder_env` 参数，pytest 将会自动调用 `adder_env` Fixture，并将其返回值传入测试用例。

上述代码中自定义了一个 Fixture `adder_env`，并在测试用例中进行使用，这也就意味着资源的管理工作都将会在 Fixture 中完成，测试用例只需要关注测试逻辑即可。`adder_env` 必须使用 toffee-test 提供的 `toffee_request` Fixture 作为参数，以便进行资源管理，`toffee_request` 提供了一系列的方法来管理资源。

- 通过 `add_cov_groups` 添加覆盖组，toffee-test 会自动将其生成至报告中。
- 通过 `create_dut` 创建 DUT 实例，toffee-test 会自动管理 DUT 的波形文件和覆盖率文件的生成，并确保文件名称不产生冲突，还会自动初始化时钟引脚。

在 `adder_env` 中，可以自定义返回值传入测试用例中。如果想要任意文件中的测试用例都可以访问到该 Fixture，可以将 Fixture 定义在 `conftest.py` 文件中。

需要注意的是，如果使用了 Toffee 的验证环境 Env，需要**确保 Env 在 Fixture 中创建**。不可以在测试用例中创建 Env，否则无法保证协程调度的正确性。

至此，我们实现了测试用例资源管理和逻辑编写的分离，无需在每个测试用例中手动管理资源的创建与释放。

## 生成测试报告

在运行 pytest 时，toffee 会自动收集测试用例的执行结果，自动统计覆盖率信息，并生成一个验证报告，想要生成该报告，需要在调用 pytest 时添加 `--toffee-report` 参数。

```bash
pytest --toffee-report
```

默认情况下，toffee 将会为每次运行生成一个默认报告名称，并将报告放至 `reports` 目录下。可以通过 `--report-dir` 参数来指定报告的存放目录，通过 `--report-name` 参数来指定报告的名称。
