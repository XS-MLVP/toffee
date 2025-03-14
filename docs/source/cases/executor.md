# 如何使用测试环境接口进行驱动

## 如何同时调用多个驱动函数

当验证环境搭建完成后，可以通过验证环境提供的接口来编写测试用例。然而，通过普通的串行代码，往往无法完成两个驱动函数的同时调用。在多个接口需要同时驱动的情况下，这种情况变得尤为重要，toffee 为这种场景提供了简便的调用方式。

### 同时调用多个不同类别的驱动函数

例如目前的 Env 结构如下：

```
DualPortStackEnv
  - port1_agent
    - @driver_method push
    - @driver_method pop
  - port2_agent
    - @driver_method push
    - @driver_method pop
```

我们期望在测试用例中同时调用 `port1_agent` 和 `port2_agent` 的 `push` 函数，以便同时驱动两个接口。

在 toffee 中，可以通过 `Executor` 来完成。

```python
from toffee import Executor

def test_push(env):
    async with Executor() as exec:
        exec(env.port1_agent.push(1))
        exec(env.port2_agent.push(2))

    print("result", exec.get_results())
```

我们使用 `async with` 来创建一个 `Executor` 对象，并建立一个执行块，通过直接调用 `exec` 可以添加需要执行的驱动函数。当 `Executor` 对象退出作用域时，会将所有添加的驱动函数同时执行。`Executor` 会自动等待所有驱动函数执行完毕。

如果需要获取驱动函数的返回值，可以通过 `get_results` 方法来获取，`get_results` 会以字典的形式返回所有驱动函数的返回值，其中键为驱动函数的名称，值为一个列表，列表中存放了对应驱动函数的返回值。

### 同一驱动函数被多次调用

如果在在执行块中多次调用同一驱动函数，`Executor` 会自动将这些调用串行执行。

```python
from toffee import Executor

def test_push(env):
    async with Executor() as exec:
        for i in range(5):
            exec(env.port1_agent.push(1))
        exec(env.port2_agent.push(2))

    print("result", exec.get_results())
```

例如上述代码中，`port1_agent.push` 会被调用 5 次，`port2_agent.push` 会被调用 1 次。由于 `port1_agent.push` 是同一驱动函数，`Executor` 会自动将这 10 次调用串行执行，其返回值会被依次存放在返回值列表中。通过，`port2_agent.push` 将会与 `port1_agent.push` 并行执行。

上述过程中，我们创建了这样一个调度过程：

```
------------------  current time --------------------
  +---------------------+   +---------------------+
  | group "agent1.push" |   | group "agent2.push" |
  | +-----------------+ |   | +-----------------+ |
  | |   agent1.push   | |   | |   agent2.push   | |
  | +-----------------+ |   | +-----------------+ |
  | +-----------------+ |   +---------------------+
  | |   agent1.push   | |
  | +-----------------+ |
  | +-----------------+ |
  | |   agent1.push   | |
  | +-----------------+ |
  | +-----------------+ |
  | |   agent1.push   | |
  | +-----------------+ |
  | +-----------------+ |
  | |   agent1.push   | |
  | +-----------------+ |
  +---------------------+
------------------- Executor exit -------------------
```

Executor 根据两个驱动函数的函数名自动创建了两个调度组，并按照调用顺序将驱动函数添加到对应的调度组中。在调度组内部，驱动函数会按照添加的顺序依次执行。在调度组之间，驱动函数会并行执行。

调度组的默认名称为以 `.` 分隔的驱动函数路径名。

通过 `sche_group` 参数，你可以在执行函数时手动指定驱动函数调用时所属的调度组，例如

```python
from toffee import Executor

def test_push(env):
    async with Executor() as exec:
        for i in range(5):
            exec(env.port1_agent.push(1), sche_group="group1")
        exec(env.port2_agent.push(2), sche_group="group1")

    print("result", exec.get_results())
```

这样一来，`port1_agent.push` 和 `port2_agent.push` 将会被按顺序添加到同一个调度组 `group1` 中，表现出串行执行的特性。同时 `get_results` 返回的字典中，`group1` 会作为键，其值为一个列表，列表中存放了 `group1` 中所有驱动函数的返回值。

### 将自定义函数加入 Executor

如果我们在一个自定义函数中调用了驱动函数或其他驱动函数，并希望自定义函数也可以通过 `Executor` 来调度，可以通过与添加驱动函数相同的方式来添加自定义函数。

```python
from toffee import Executor

async def multi_push_port1(env, times):
    for i in range(times):
        await env.port1_agent.push(1)

async def test_push(env):
    async with Executor() as exec:
        for i in range(2):
            exec(multi_push_port1(env, 5))
        exec(env.port2_agent.push(2))

    print("result", exec.get_results())
```

此时，`multi_push_port1` 会被添加到 `Executor` 中，并创建以 `multi_push_port1` 为名称的调度组，并向其中添加两次调用。其会与 `port2_agent.push` 调度组并行执行。

我们也可以在自定义函数中使用 `Executor`，或调用其他自定义函数。这样一来，我们可以通过 `Executor` 完成任意复杂的调度。以下提供了若干个案例：

**案例一**

环境接口如下：

```
Env
- agent1
    - @driver_method send
- agent2
    - @driver_method send
```

两个 Agent 中的 `send` 函数各需要被并行调用 5 次，并且调用时需要发送上一次的返回结果，第一次发送时发送 0，两个函数调用相互独立。

```python
from toffee import Executor

async def send(agent):
    result = 0
    for i in range(5):
        result = await agent.send(result)

async def test_send(env):
    async with Executor() as exec:
        exec(send(env.agent1), sche_group="agent1")
        exec(send(env.agent2), sche_group="agent2")

    print("result", exec.get_results())
```

**案例二**

环境接口如下：

```
env
- agent1
    - @driver_method long_task
- agent2
    - @driver_method task1
    - @driver_method task2
```

task1 和 task2 需要并行执行，并且一次调用结束后需要同步，task1 和 task2 都需要调用 5 次，long_task 需要与 task1 和 task2 并行执行。

```python
from toffee import Executor

async def exec_once(env):
    async with Executor() as exec:
        exec(env.agent2.task1())
        exec(env.agent2.task2())

async def test_case(env):
    async with Executor() as exec:
        for i in range(5):
            exec(exec_once(env))
        exec(env.agent1.long_task())

    print("result", exec.get_results())
```

### 设置 Executor 的退出条件

Executor 会等待所有添加的驱动函数执行完毕后退出，但有时我们并不需要等待所有驱动函数执行完毕，可以通过在创建 Executor 时使用 `exit` 参数来设置退出条件。

`exit` 参数可以被设置为 `all`, `any` 或 `none` 三种值，分别表示所有调度组执行完毕后退出、任意一个调度组执行完毕后退出、不等待直接退出。

```python
from toffee import Executor

async def send_forever(agent):
    result = 0
    while True:
        result = await agent.send(result)

async def test_send(env):
    async with Executor(exit="any") as exec:
        exec(send_forever(env.agent1))
        exec(env.agent2.send(1))

    print("result", exec.get_results())
```

例如上述代码中 `send_forever` 函数是一个无限循环的函数，将 `exit` 设置为 `any` 后，Executor 会在 `env.agent2.send` 函数执行完毕后退出，而不会等待 `send_forever` 函数执行完毕。

如果后续需要等待所有任务执行完毕，可以通过等待 `exec.wait_all` 来实现。

## 如何控制参考模型调度

在 toffee 中，参考模型的调度是由 toffee 自动完成的，但在某些情况下需要手动控制参考模型的调度顺序，例如在参考模型中需要调用多个函数，且这些函数之间存在调用顺序的情况。或者是控制参考模型与驱动函数之间的调用顺序。

### 参考模型的调度顺序

在使用 Executor 执行时，可以使用参数 `sche_order` 来控制参考模型是在驱动函数之前、之后或同时执行。当为 `model_first` 时，参考模型会在驱动函数之前执行；当为 `dut_first` 时，驱动函数会在参考模型之前执行；当为 `parallel` 时，参考模型会与驱动函数同时执行。默认情况下为并行执行。

```python
def test_push(env):
    async with Executor() as exec:
        exec(env.port1_agent.push(1), sche_order="dut_first")
        exec(env.port2_agent.push(2), sche_order="dut_first")

    print("result", exec.get_results())
```

上述代码中，参考模型将会在对应的驱动函数结束之后才会被调用。

### 参考模型函数之间的调用顺序

当使用函数调用模式编写参考模型时，参考模型中的函数之间可能存在调用顺序相关的一来，例如一个函数在调用之前必须需要另一个函数先被调用。

这一过程若不使用 Executor 使函数并行执行，很容易得到控制，串行执行的代码中函数的调用顺序即为其执行顺序。

但如果使用 Executor 并行执行函数，两个参考模型之间的调用顺序就无法保证。toffee 为此场景提供了 `priority` 参数，用于指定参考模型函数的调用顺序，数值越小其优先级较高。

```python
from toffee import Executor

def test_push(env):
    async with Executor() as exec:
        exec(env.port1_agent.push(1), priority=1)
        exec(env.port2_agent.push(2), priority=0)

    print("result", exec.get_results())
```

例如上述代码中，`port2_agent.push` 和 `port1_agent.push` 两个函数会并行执行，其参考模型的调用也将在同一时钟周期内完成。由于我们指定了`port2_agent.push` 的优先级为 0，`port1_agent.push` 的优先级为 1，因此在该周期的执行过程中，`port2_agent.push` 会优先被调用。

注意，优先级只在同一时钟周期内有效，若两个函数调用跨越了时钟周期，那么时钟周期靠前的函数依然会被优先调用。
