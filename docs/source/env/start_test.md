# 如何使用异步环境

## 异步编程

在如前介绍的验证环境中，设计了一套规范的验证环境。但是如果尝试用朴素单线程程序编写，会发现会遇到较为复杂的实现问题。

例如，我们创建了两个驱动方法分别驱动两个接口，在每一个驱动方法内部，都需要等待 DUT 经过若干个时钟周期，并且这两个驱动方法需要同时运行。这时候，如果使用朴素的单线程程序，会发现很难同时让两个驱动方法同时运行，即便我们使用多线程强制使他们同时运行，也会发现缺乏一种机制，使他们能够等待 DUT 经过若干时钟周期。这是因为在 Picker 提供的接口中，我们只能去推动 DUT 向前执行一个周期，而无法去等待 DUT 执行一个周期，更不用说我们还会遇到有众多环境组件需要同时运行的情况了。

因此，我们首先需要一个能够运行异步程序的环境。toffee 使用了 Python 的协程来完成对异步程序的管理，其在**单线程**之上建立了一个**事件循环（EventLoop）**，用于管理多个同时运行的协程，协程之间可以相互等待并通过事件循环来进行切换。协程可以看作是一种轻量级的线程，它可以在一个线程中并发执行，但是不会引入线程切换的开销。

在启动事件循环之前，我们首先需要了解两个关键字 `async` 和 `await` 来了解 Python 对与协程的管理。

当我们在函数前加上 `async` 关键字时，这个函数就变成了一个协程函数，例如

```python
async def my_coro():
    ...
```

当我们在协程函数内部使用 `await` 关键字时，我们就可以执行一个协程函数，并等待其执行完成并返回结果，例如

```python
async def my_coro():
    return "my_coro"

async def my_coro2():
    result = await my_coro()
    print(result)
```

如果不想等待一个协程函数完成，只想将这一函数加入到事件循环中放入后台运行，可以使用 toffee 提供的 `create_task` 方法，例如

```python
import toffee

async def my_coro():
    return "my_coro"

async def my_coro2():
    toffee.create_task(my_coro())
```

## 启动事件循环

那么如何启动事件循环，并使事件循环开始运行 `my_coro2` 呢？在 toffee 中，我们使用 `toffee.run` 来启动事件循环，并运行异步程序。

```python
import toffee

toffee.run(my_coro2)
```

toffee 中的环境组件都需要在事件循环中运行，因此当启动 toffee 验证环境时，必须通过 `toffee.run` 先启动事件循环，然后在事件循环中去创建 toffee 验证环境。

因此，在验证环境创建时，应该以类似如下的方式：

> 创建方式相较早期版本有改动
> 如果需要使用 toffee.run 来启动事件循环，需要编写一个句柄函数，用于创建验证环境，然后将这个句柄函数传入 toffee.run 中。这可以保证验证环境在 Test 开始之前被创建，以维持调度顺序需求。句柄函数的返回值将会被传入 Test 函数中。同时使用这种方式，应该将异步函数本身作为第一参数传入。
> 如果使用了 [如何使用 Pytest 管理测试用例](cases/pytest.md) 中 Fixture 的方式创建验证环境，则无需关注此问题。

```python
import toffee

async def start_test(env):
    ...

def env_handle():
    # 创建验证环境
    return MyEnv()

toffee.run(start_test, env_handle)
```

## 如何管理 DUT 时钟

正如开头提出的问题，如果我们需要两个驱动方法同时运行，并且在每个驱动方法需要等待 DUT 经过若干个时钟周期。异步环境给予了我们等待某个事件的能力，但 Picker 只提供了推动 DUT 向前执行一个周期的能力，没有有提供一个事件让我们来等待。

toffee 中提供了对这类功能的支持，它通过创建一个后台时钟，来实现对 DUT 进行一个个周期的向前推动，每推动一个周期，后台时钟就会向其他协程发出时钟信号，使得其他协程能够继续执行。因此，DUT 的实际执行周期推动是由后台时钟来完成的，其他协程中只需要等待后台时钟发布的时钟信号即可。

在 toffee 中，通过`start_clock`来创建后台时钟：

```python
import toffee

async def start_test():
    dut = MyDUT()
    toffee.start_clock(dut)

toffee.run(start_test)
```

只需要在事件循环中调用 `start_clock` 即可创建后台时钟，它需要一个 DUT 对象作为参数，用于推动 DUT 的执行，以及将时钟信号绑定到 DUT 以及其各个引脚。

在后台时钟创建完成后，我们就可以在任意协程中通过 `ClockCycles` 来等待时钟信号到来，`ClockCycles` 的参数可以是 DUT，也可以是 DUT 的每一个引脚。例如：

```python
import toffee
from toffee.triggers import *

async my_coro(dut):
    await ClockCycles(dut, 10)
    print("10 cycles passed")

async def start_test():
    dut = MyDUT()
    toffee.start_clock(dut)

    await my_coro(dut)

toffee.run(start_test)
```

在 `my_coro` 中，通过 `ClockCycles` 来等待 DUT 经过 10 个时钟周期，当 10 个时钟周期经过后，`my_coro` 就会继续执行，并打印 "10 cycles passed"。

toffee 中提供了多种等待时钟信号的方法，例如：

- `ClockCycles` 等待 DUT 经过若干个时钟周期
- `Value` 等待 DUT 的某个引脚的值等于某个值
- `AllValid` 等待 DUT 的所有引脚的值同时有效
- `Condition` 等待某个条件满足
- `Change` 等待 DUT 的某个引脚的值发生变化
- `RisingEdge` 等待 DUT 的某个引脚的上升沿
- `FallingEdge` 等待 DUT 的某个引脚的下降沿

更多等待时钟信号的方法，参见 [toffee.triggers module](/api/toffee.rst#module-toffee.triggers)。
