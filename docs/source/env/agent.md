# 如何编写 Agent

`Agent` 在 toffee 验证环境中实现了对一类 `Bundle` 中信号的高层封装，使得上层驱动代码可以在不关心具体信号赋值的情况下，完成对 Bundle 中信号的驱动及监测。

一个 `Agent` 由 **驱动方法(driver_method)** 和 **监测方法(monitor_method)** 组成，其中驱动方法用于主动驱动 `Bundle` 中的信号，而监测方法用于被动监测 `Bundle` 中的信号。

## 初始化 Agent

为了定义一个 `Agent`，需要自定义一个新类，并继承 toffee 中的 `Agent` 类。下面是一个简单的 `Agent` 的定义示例：

```python
from toffee.agent import *

class AdderAgent(Agent):
    def __init__(self, bundle):
        super().__init__(bundle.step)
        self.bundle = bundle
```

在 `AdderAgent` 类初始化时，需要外界传入该 Agent 需要驱动的 Bundle，并且需要向父类 `Agent` 中传入一个时钟同步函数，以便 `Agent` 使用这一函数来决定何时调用监测方法。一般来说，可以将其设置为 `bundle.step`，即 `Bundle` 中的时钟同步函数，`Bundle` 中的 step 与 DUT 中的时钟同步。

## 创建驱动方法

在 `Agent` 中，驱动方法是一个异步函数，用于主动驱动 `Bundle` 中的信号。驱动函数需要将函数的传入参数进行解析，并根据解析结果对 `Bundle` 中的信号进行赋值，赋值的过程可以跨越多个时钟周期。如果需要获取 Bundle 的信号值，那么在函数中编写相应的逻辑，并将其转换为需要的数据，通过函数返回值返回。

每一个驱动方法都应是一个异步函数，并且使用 `@driver_method` 装饰器进行修饰，以便 `Agent` 能够识别该函数为驱动方法。

下面是一个简单的驱动方法的定义示例：

```python
from toffee.agent import *

class AdderAgent(Agent):
    def __init__(self, bundle):
        super().__init__(bundle.step)
        self.bundle = bundle

    @driver_method()
    async def exec_add(self, a, b, cin):
        self.bundle.a.value = a
        self.bundle.b.value = b
        self.bundle.cin.value = cin
        await self.bundle.step()
        return self.bundle.sum.value, self.bundle.cout.value
```

在 `drive` 函数中，我们将传入的 `a`, `b`, `cin` 三个参数分别赋值给 `Bundle` 中的 `a`, `b`, `cin` 信号，并等待一个时钟周期。在时钟周期结束后，我们返回 `Bundle` 中的 `sum`, `cout` 信号值。

在驱动函数的编写过程中，你可以使用 [如何使用异步环境](start_test.md) 中介绍的所有等待时钟信号的同步方法，例如 `ClockCycles`, `Value` 等。

创建完毕后，你可以像调用普通函数一样在驱动代码中调用该驱动方法，例如：

```python
adder_bundle = AdderBundle()
adder_agent = AdderAgent(adder_bundle)
sum, cout = await adder_agent.exec_add(1, 2, 0)
print(sum, cout)
```

被标识为 `@driver_method` 的函数在调用时拥有诸多特性，这一部分将在编写测试用例中详细介绍。同时，该类函数还会完成参考模型的匹配与自动调用以返回值对比，这一部分将在编写参考模型中详细介绍。

## 创建监测方法

监测方法同样需要是一个异步函数，并且使用 `@monitor_method` 装饰器进行修饰，以便 `Agent` 能够识别该函数为监测方法。

一个简单的监测方法的定义示例如下：

```python
from toffee.agent import *

class AdderAgent(Agent):
    def __init__(self, bundle):
        super().__init__(bundle.step)
        self.bundle = bundle

    @monitor_method()
    async def monitor_sum(self):
        if self.bundle.sum.value > 0:
            return self.bundle.as_dict()
```

在 `monitor_sum` 函数中，我们以 Bundle 中的 sum 信号作为监测对象，当 sum 信号的值大于 0 时，收集 Bundle 生成的默认消息类型，收集到的返回值将会被存储到内部的消息队列中。

添加 `monitor_method` 装饰器后，`monitor_sum` 函数将会被 `Agent` 自动调用，它会使用 `Agent` 初始化时提供的时钟同步函数来决定何时调用监测方法。默认情况下，`Agent` 会在每个时钟周期都调用一次监测方法，如果监测方法有返回值，那么返回值将会被存储到内部的消息队列中。若监测方法的一次调用会经过多个时钟周期，`Agent` 会等待上一次监测方法调用结束后再次调用监测方法。

如果编写了类似下面的监测方法：

```python
@monitor_method()
async def monitor_sum(self):
    return self.bundle.as_dict()
```

该监测方法将会在每个周期都往消息队列中添加一个消息。

**获取监测消息**

由于该监测方法被标记为了 `@monitor_method`，因此该方法将会被 `Agent` 自动调用，在测试用例中如果按照以下方式直接调用该函数，并不能执行该函数的预期行为。

```python
adder_bundle = AdderBundle()
adder_agent = AdderAgent(adder_bundle)
result = await adder_agent.monitor_sum()
```

相反的，按照上述方式调用监测方法，它将会弹出消息队列中收集到的最早的消息，并返回该消息。如果消息队列为空，该次调用将会等待消息队列中有消息后再返回。

如果想获取消息队列中的消息数量，可以使用如下方式获取：

```python
message_count = adder_agent.monitor_size("monitor_sum")
```

通过创建监测方法，你可以方便地添加一个后台监测任务，监测 `Bundle` 中的信号值，并在满足条件时收集消息。将函数标记为监测方法后，框架还会为这一方法提供与参考模型的匹配与自动收集对比，这一部分将在编写参考模型中详细介绍。

通过在 Agent 中编写多个驱动方法和监测方法，便完成了整个 `Agent` 的编写。
