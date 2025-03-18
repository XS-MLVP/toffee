# 如何编写 Agent

`Agent` 在 toffee 验证环境中实现了对一类 `Bundle` 中信号的高层封装，使得上层驱动代码可以在不关心具体信号赋值的情况下，完成对 Bundle 中信号的驱动及监测。

一个 `Agent` 由 **驱动方法(driver_method)** 和 **监测方法(monitor_method)** 组成，其中驱动方法用于主动驱动 `Bundle` 中的信号，而监测方法用于被动监测 `Bundle` 中的信号。

## 初始化 Agent

为了定义一个 `Agent`，需要自定义一个新类，并继承 toffee 中的 `Agent` 类，例如：

```python
from toffee import Agent

class AdderAgent(Agent):
    ...
```

在 `AdderAgent` 类初始化时，需要外界传入该 Agent 需要驱动的 Bundle，例如：

```python
adder_agent = AdderAgent(adder_bundle)
```

## 创建驱动方法

在 `Agent` 中，驱动方法是一个异步函数，用于主动驱动 `Bundle` 中的信号。驱动函数需要将函数的传入参数进行解析，并根据解析结果对 `Bundle` 中的信号进行赋值，赋值的过程可以跨越多个时钟周期。如果需要获取 Bundle 的信号值，那么在函数中编写相应的逻辑，并将其转换为需要的数据，通过函数返回值返回。

每一个驱动方法都应是一个异步函数，并且使用 `@driver_method` 装饰器进行修饰，以便 `Agent` 能够识别该函数为驱动方法。

下面是一个简单的驱动方法的定义示例：

```python
from toffee.agent import *

class AdderAgent(Agent):
    @driver_method()
    async def exec_add(self, a, b, cin):
        self.bundle.a.value = a
        self.bundle.b.value = b
        self.bundle.cin.value = cin
        await self.bundle.step()
        return self.bundle.sum.value, self.bundle.cout.value
```

在 `drive` 函数中，我们将传入的 `a`, `b`, `cin` 三个参数分别赋值给 `Bundle` 中的 `a`, `b`, `cin` 信号，并等待一个时钟周期。在时钟周期结束后，返回 `Bundle` 中的 `sum`, `cout` 信号值。

在驱动函数的编写过程中，你可以使用 [如何使用异步环境](start_test.md) 中介绍的所有等待时钟信号的同步方法，例如 `ClockCycles`, `Value` 等。

创建完毕后，你可以像调用普通函数一样在驱动代码中调用该驱动方法，例如：

```python
adder_bundle = AdderBundle()
adder_agent = AdderAgent(adder_bundle)
sum, cout = await adder_agent.exec_add(1, 2, 0)
print(sum, cout)
```

被标识为 `@driver_method` 的函数在调用时拥有诸多特性，其将会完成参考模型的匹配与自动调用以返回值对比，这一部分将在[如何编写参考模型](/env/ref_model.md)中详细介绍。

## 创建监测方法

监测方法同样需要是一个异步函数，并且使用 `@monitor_method` 装饰器进行修饰，以便 `Agent` 能够识别该函数为监测方法。

一个简单的监测方法的定义示例如下：

```python
from toffee.agent import *

class AdderAgent(Agent):
    @monitor_method()
    async def monitor_sum(self):
        if self.bundle.sum.value > 0:
            return self.bundle.as_dict()
```

在 `monitor_sum` 函数中，我们以 Bundle 中的 sum 信号作为监测对象，当 sum 信号的值大于 0 时，收集 Bundle 中各个信号的数据。
添加 `monitor_method` 装饰器后，`monitor_sum` 函数将会被 `Agent` 自动调用。

每个时钟周期开始时，所有监测方法都会被调用一次，如果监测方法**返回值不为空**，则表示一次成功监测，其返回值将被视作监测到的数据以待后续使用。若监测方法的一次调用会经过多个时钟周期，`Agent` 会等待上一次监测方法调用结束后再次调用监测方法。

例如，如果编写了类似下面的监测方法：

```python
@monitor_method()
async def monitor_sum(self):
    return self.bundle.as_dict()
```

该监测方法将会在每个周期都获取 `Bundle` 中的信号值，并将其返回。

**获取监测消息**

由于该监测方法被标记为了 `@monitor_method`，因此该方法将会被 `Agent` 自动调用。监测到的数据会被存放在内部的消息队列当中，在测试用例中，你可以通过调用 `monitor_sum` 方法来获取该监测方法监测到的数据，例如：

```python
result = await adder_agent.monitor_sum()
```

这将会弹出消息队列中收集到的最早的消息，并返回该消息。如果消息队列为空，该次调用将会等待消息队列中有消息后再返回。

> 此处与早期版本不同，需要手动开启消息队列监测功能

需要注意的是，如果需要启动该功能，需要在测试开始之前通过 `start_monitor` 方法启动消息队列监测功能，并指定消息队列的大小，例如：

```python
adder_agent.start_monitor("monitor_sum", 10)
```

此外，如果想获取消息队列中的消息数量，可以使用如下方式获取：

```python
message_count = adder_agent.monitor_size("monitor_sum")
```

通过创建监测方法，你可以方便地添加一个后台监测任务，监测 `Bundle` 中的信号值，并在满足条件时收集消息。将函数标记为监测方法后，框架还会为这一方法提供与参考模型相关的对比，这一部分将在编写参考模型中详细介绍。

## Agent 中的时序

Agent 中监测方法和驱动方法的时序遵循以下两条规则：

1. 每周期开始时，所有监测方法优先被调用，所有监测方法调用结束后才会调用驱动方法。

2. 在测试整个流程中，驱动方法在第 0 个周期就可以被调用，监测方法则会在第1个周期起始时开始被框架调用，测试结束时，最后一个周期监测方法会被额外调用一次。例如，验证环境中仅使用上述代码中的 `exec_add` 方法和 `monitor_sum` 方法，测试流程为执行五次 `exec_add` 方法，那么测试流程如下：

    ```plaintext
                cycle 0       cycle 1            cycle 2          cycle 3          cycle 4          cycle 5
    test start ---------+-----------------+-----------------+-----------------+-----------------+----------------- test end
                        |                 |                 |                 |                 |
                +---------------+ +---------------+ +---------------+ +---------------+ +---------------+
                |    exec_add   | |    exec_add   | |    exec_add   | |    exec_add   | |    exec_add   |
                +---------------+ +---------------+ +---------------+ +---------------+ +---------------+
                        | +-------------+ | +-------------+ | +-------------+ | +-------------+ | +-------------+
                        | | monitor_sum | | | monitor_sum | | | monitor_sum | | | monitor_sum | | | monitor_sum |
                        | +-------------+ | +-------------+ | +-------------+ | +-------------+ | +-------------+
                        |                 |                 |                 |                 |
               ---------+-----------------+-----------------+-----------------+-----------------+-----------------
    ```
