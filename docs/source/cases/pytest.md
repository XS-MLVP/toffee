# 如何使用 Pytest 管理测试用例

## 编写测试用例

在 toffee 中，测试用例是通过 pytest 来管理的。pytest 是一个功能强大的 Python 测试框架，如果你不熟悉 pytest，可以查看 [pytest 官方文档](https://docs.pytest.org/en/latest/)。

### 编写第一个测试用例

首先，我们需要创建一个测试用例文件，例如 `test_adder.py`，该文件需要以 `test_` 开头，或以 `_test.py` 结尾，以便 pytest 能够识别。接着可以在其中编写我们的第一个测试用例。

```python
# test_adder.py

async def my_test():
    env = AdderEnv()
    env.add_agent.exec_add(1, 2, 0)

def test_adder():
    toffee.run(my_test())
```

pytest 并不能直接运行协程测试用例，因此我们需要在测试用例中调用 `toffee.run` 来运行异步测试用例。

用例编写完成后，我们可以在终端中运行 pytest。

```bash
pytest
```

pytest 会查找当前目录下所有以 `test_` 开头或以 `_test.py` 结尾的文件，并运行其中以 `test_` 开头的函数，每一个函数被视作一个测试用例。

### 运行协程测试用例

为了使 pytest 能够直接运行协程测试用例，toffee 提供了 `toffee_async` 标记来标记异步测试用例。

```python
# test_adder.py

@pytest.mark.toffee_async
async def test_adder():
    env = AdderEnv(DUTAdder())
    await env.add_agent.exec_add(1, 2, 0)
```

如图所示，我们只需要在测试用例函数上添加 `@pytest.mark.toffee_async` 标记，pytest 就能够直接运行协程测试用例。

## 生成测试报告

在运行 pytest 时，toffee 会自动收集测试用例的执行结果，自动统计覆盖率信息，并生成一个验证报告，想要生成该报告，需要在调用 pytest 时添加 `--toffee-report` 参数。

```bash
pytest --toffee-report
```

默认情况下，toffee 将会为每次运行生成一个默认报告名称，并将报告放至 `reports` 目录下。可以通过 `--report-dir` 参数来指定报告的存放目录，通过 `--report-name` 参数来指定报告的名称。

但此时，由于 toffee 无法得知覆盖率文件名称，因此在报告中无法显示覆盖率信息，如果想要在报告中显示覆盖率信息，需要在每个测试用例中传入功能覆盖组及行覆盖率文件的名称。

```python
@pytest.mark.toffee_async
async def test_adder(request):
    adder = DUTAdder(
        waveform_filename="adder.fst",
        coverage_filename="adder.dat"
    )
    g = CovGroup("Adder")

    env = AdderEnv(adder)
    await env.add_agent.exec_add(1, 2, 0)

    adder.Finish()
    set_func_coverage(request, cov_groups)
    set_line_coverage(request, "adder.dat")
```

上述代码中，在创建 DUT 时，我们传入了波形文件和覆盖率文件的名称，使得 DUT 在运行时可以生成指定名称的覆盖率文件。接着我们定义了一个覆盖组，来收集 DUT 的功能覆盖率信息，具体如何使用将在下个文档中介绍。

接着，调用了 DUT 的 `Finish` 方法，用于结束波形文件的记录。最终我们通过 `set_func_coverage` 和 `set_line_coverage` 函数来设置功能覆盖组及行覆盖率文件信息。

此时再次运行 pytest 时，toffee 将会自动收集覆盖率信息，并在报告中显示。

## 使用 toffee-test 管理资源

然而，上述过程过于繁琐，并且为了保证每个测试用例之间文件名称不产生冲突，我们需要在每个测试用例中传入不一样的文件名称。并且在测试用例出现异常时，测试用例并不会运行完毕，导致覆盖率文件无法生成。

因此，toffee-test 提供了 `toffee_request` Fixture 来管理资源，简化了测试用例的编写。

```python
# test_adder.py

@pytest.mark.toffee_async
async def test_adder(my_request):
    dut = my_request
    env = AdderEnv(dut)
    await env.add_agent.exec_add(1, 2, 0)

@pytest.fixture()
def my_request(toffee_request: ToffeeRequest):
    toffee_request.add_cov_groups(CovGroup("Adder"))
    return toffee_request.create_dut(DUTAdder)
```

Fixture 是 pytest 中的概念，例如上述代码中定义了一个名为 `my_request` 的 Fixture。如果在其他测试用例的输出参数中含有 `my_request` 参数，pytest 将会自动调用 `my_request` Fixture，并将其返回值传入测试用例。

上述代码中自定义了一个 Fixture `my_request`，并在测试用例中进行使用，这也就意味着资源的管理工作都将会在 Fixture 中完成，测试用例只需要关注测试逻辑即可。`my_request` 必须使用 toffee-test 提供的 `toffee_request` Fixture 作为参数，以便进行资源管理，`toffee_request` 提供了一系列的方法来管理资源。

通过 `add_cov_groups` 添加覆盖组，toffee-test 会自动将其生成至报告中。
通过 `create_dut` 创建 DUT 实例，toffee-test 会自动管理 DUT 的波形文件和覆盖率文件的生成，并确保文件名称不产生冲突。

在 `my_request` 中，可以自定义返回值传入测试用例中。如果想要任意测试用例都可以访问到该 Fixture，可以将 Fixture 定义在 `conftest.py` 文件中。

至此，我们实现了测试用例资源管理和逻辑编写的分离，无需在每个测试用例中手动管理资源的创建与释放。
