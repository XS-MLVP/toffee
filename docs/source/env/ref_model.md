# 如何编写参考模型

`参考模型` 用于模拟待验证设计的行为，以便在验证过程中对设计进行验证。在 toffee 验证环境中，参考模型需要遵循 `Env` 的接口规范，以便能够附加到 `Env` 上，由 `Env` 来完成参考模型的自动同步。

## 参考模型的两种实现方式

toffee 提供了两种参考模型的实现方式，这两种方式都可以被附加到 `Env` 上，并由 `Env` 来完成参考模型的自动同步。在不同的场景下，可以选择更适合的方式来实现参考模型。

这两种方式分别是 **函数调用模式** 与 **独立执行流模式**，下面将分别介绍这两种方式的具体概念。

### 函数调用模式

**函数调用模式**即是将参考模型的对外接口定义为一系列的函数，通过调用这些函数来驱动参考模型的行为。此时，我们通过输入参数向参考模型发送数据，并通过返回值获取参考模型的输出数据，参考模型通过函数体的逻辑来更新内部状态。

下面是一个简单的函数调用模式的参考模型的定义示例：

例如，这是一个简单的加法器参考模型：

```python
class AdderRefModel():
    def add(self, a, b):
        return a + b
```

在这个参考模型中，不需要任何内部状态，通过一个对外函数接口即可实现参考模型所有功能。

需要注意的是，使用函数调用模式编写的参考模型，只能通过外部主动调用的方式来执行，无法被动输出内部数据。因此，其无法与 Agent 中的监测方法进行匹配。在 Agent 中编写监测方法，在函数调用模式编写参考模型时是没有意义的。

### 独立执行流模式

**独立执行流模式**即是将参考模型的行为定义为一个独立的执行流，它不再受外部主动调用函数控制，而拥有了主动获取输入数据和主动输出数据的能力。当外部给参考模型发送数据时，参考模型不会立即响应，而是将这一数据保存起来，等待其执行逻辑主动获取该数据。

我们用一段代码来说明这种模式，该示例中用到了 toffee 中提供的相关概念来实现，但目前无需关心这些概念的使用细节。

```python
class AdderRefModel(Model):
    def __init__(self):
        super().__init__()

        self.add_port = DriverPort()
        self.sum_port = MonitorPort()

    async def main():
        while True:
            operands = await self.add_port()
            sum = operands["a"] + operands["b"]
            await self.sum_port(sum)
```

在这里，我们在参考模型构造函数中定义了两类接口，一类为**驱动接口(DriverPort)**，即代码中的`add_port`，用于接收外部输入数据；另一类为**监测接口(MonitorPort)**，即代码中的`sum_port`，用于向外部输出数据。

定义了这两个接口后，上层代码在给参考模型发送数据时，并不会触发参考模型中的某个函数，而是会将数据发送到 `add_port` 这个驱动接口中。同时，上层代码也无法主动获取到参考模型的输出数据了。参考模型的输出数据会通过 `sum_port` 这个监测接口，由参考模型主动输出。

那么参考模型如何去使用这两个接口呢？在参考模型中，有一个 main 函数，这是参考模型执行的入口，当参考模型创建时, main 函数会被自动调用，并在后台持续运行。在上面代码中 main 函数里，参考模型通过不断重复这一过程：等待 `add_port` 中的数据、计算结果、将结果输出到 `sum_port` 中 来实现参考模型的行为。

参考模型会主动向 `add_port` 请求数据，如果 `add_port` 中没有数据，参考模型会等待数据的到来。当数据到来后，参考模型将会进行计算，将计算结果主动的输出到 `sum_port` 中。它的执行过程是一个独立的执行流，不受外部的主动调用控制。当参考模型变得复杂时，其将会含有众多的驱动接口和监测接口，通过独立执行流的方式，可以更好的去处理结构之间的相互关系，尤其是接口之间存在调用顺序的情况。

## 如何编写函数调用模式的参考模型

### 驱动函数匹配

假如 Env 中定义的接口如下：

```
StackEnv
  - port_agent
    - @driver_method push
    - @driver_method pop
```

那么如果我们想要编写与之对应的参考模型，自然地，我们需要定义这四个驱动函数被调用时参考模型的行为。也就是说为每一个驱动函数编写一个对应的函数，这些函数将会在驱动函数被调用时被框架自动调用。

如何让参考模型中定义的函数能够与某个驱动函数匹配呢？首先应该使用 `@driver_hook` 装饰器来表示这个函数是一个驱动函数的匹配函数。接着，为了建立对应关系，我们需要在装饰器中指定其对应的 Agent 和驱动函数的名称。最后，只需要保证函数的参数与驱动函数的参数一致，两个函数便能够建立对应关系。

```python
class StackRefModel(Model):
    @driver_hook(agent_name="port_agent", driver_name="push")
    def push(self, data):
        pass

    @driver_hook(agent_name="port_agent", driver_name="pop")
    def pop(self):
        pass
```

此时，驱动函数与参考模型的对应关系已经建立，当 Env 中的某个驱动函数被调用时，参考模型中对应的函数将会被自动调用，并自动对比两者的返回值是否一致。

toffee 还提供了以下几种匹配方式，以便更好地匹配驱动函数：

**指定驱动函数路径**

可以通过 "." 来指定驱动函数的路径，例如：

```python
class StackRefModel(Model):
    @driver_hook("port_agent.push")
    def push(self, data):
        pass

    @driver_hook("port_agent.pop")
    def pop(self):
        pass
```

**使用函数名称匹配驱动函数名称**

如果参考模型中的函数名称与驱动函数名称相同，可以省略 `driver_name` 参数，例如：

```python
class StackRefModel(Model):
    @driver_hook(agent_name="port_agent")
    def push(self, data):
        pass

    @driver_hook(agent_name="port_agent")
    def pop(self):
        pass
```

**使用函数名称同时匹配 Agent 名称与驱动函数名称**

可以在函数名中通过双下划线 "\_\_" 来同时匹配 Agent 名称与驱动函数名称，例如：

```python
class StackRefModel(Model):
    @driver_hook()
    def port_agent__push(self, data):
        pass

    @driver_hook()
    def port_agent__pop(self):
        pass
```

### Agent 匹配

除了对 Agent 中每一个驱动函数都编写一个 `driver_hook` 之外，还可以通过 `@agent_hook` 装饰器来一次性匹配 Agent 中的所有驱动函数。

```python
class StackRefModel(Model):
    @agent_hook("port_agent")
    def port_agent(self, driver_name, args):
        pass
```

在这个例子中，`port_agent` 函数将会匹配 `port_agent` Agent 中的所有驱动函数，当 Agent 中的任意一个驱动函数被调用时，`port_agent` 函数将会被自动调用。除了 self 之外，`port_agent` 函数还需接受且只接受两个参数，第一个参数为驱动函数的名称，第二个参数为驱动函数的参数。

当某个驱动函数被调用时，driver_name 参数将会传入驱动函数的名称，args 参数将会传入该该驱动函数被调用时的参数，参数将会以字典的形式传入。port_agent 函数可以根据 driver_name 和 args 来决定如何处理这个驱动函数的调用，并将结果返回。此时框架将会使用此函数的返回值与驱动函数的返回值进行对比。

与驱动函数类似，`@agent_hook` 装饰器也支持当函数名与 Agent 名称相同时省略 `agent_name` 参数。

```python
class StackRefModel(Model):
    @agent_hook()
    def port_agent(self, driver_name, args):
        pass
```

**agent_hook 与 driver_hook 同时存在**

当 `agent_hook` 被定义后，理论上无需再定义任何 `driver_hook` 与 Agent 中的驱动函数进行匹配。但是，如果需要对某个驱动函数进行特殊处理，可以再定义一个 `driver_hook` 与该驱动函数进行匹配。

当 `agent_hook` 与 `driver_hook` 同时存在时，框架会优先调用 `agent_hook` 函数，再调用 `driver_hook` 函数，并将 `driver_hook` 函数的返回值用于结果的对比。

当 Env 中所有的驱动函数都能找到对应的 `driver_hook` 或 `agent_hook` 时，参考模型便能成功与 Env 建立匹配关系，此时可以直接通过 Env 中的 `attach` 方法将参考模型附加到 Env 上。

## 如何编写独立执行流模式的参考模型

独立执行流模式的参考模型是通过 `port` 接口的形式来完成数据的输入输出，他可以主动向 `port` 请求数据，也可以主动向 `port` 输出数据。在 toffee 中，我们提供了两种接口来实现这一功能，分别是 `DriverPort` 和 `MonitorPort`。

类似地，我们需要定义一系列的 `DriverPort` 使其与 Env 中的驱动函数匹配，同时定义一系列的 `MonitorPort` 使其与 Env 中的监测函数匹配。

当 Env 中的驱动函数被调用时，调用数据将会被发送到 `DriverPort` 中，参考模型将会主动获取这些数据，并进行计算。计算结果将会被输出到 `MonitorPort` 中，当 Env 中的监测函数被调用时，比较器会自动从 `MonitorPort` 中获取数据，并与 Env 中的监测函数的返回值进行比较。

### 驱动方法接口匹配

为了接收到 Env 中所有的驱动函数的调用，参考模型可以选择为每一个驱动函数编写对应的 `DriverPort`。可以通过 `DriverPort` 的参数 `agent_name` 与 `driver_name` 来匹配 Env 中的驱动函数。

```python
class StackRefModel(Model):
    def __init__(self):
        super().__init__()

        self.push_port = DriverPort(agent_name="port_agent", driver_name="push")
        self.pop_port = DriverPort(agent_name="port_agent", driver_name="pop")
```

与 `driver_hook` 类似，也可以使用下面的方式来匹配 Env 中的驱动函数：

```python
# 使用 "." 来指定驱动函数的路径
self.push_port = DriverPort("port_agent.push")

# 如果参考模型中的变量名称与驱动函数名称相同，可以省略 driver_name 参数
self.push = DriverPort(agent_name="port_agent")

# 使用变量名称同时匹配 Agent 名称与驱动函数名称，并使用 `__` 分隔
self.port_agent__push = DriverPort()
```

### Agent 接口匹配

也可以选择定义 `AgentPort` 同时匹配一个 Agent 中的所有驱动函数。但与 `agent_hook` 不同的是，定义了 `AgentPort` 后，便不能为该 Agent 中的任何驱动函数再定义 `DriverPort`。所有的驱动函数调用将会被发送到 `AgentPort` 中。

```python
class StackRefModel(Model):
    def __init__(self):
        super().__init__()

        self.port_agent = AgentPort(agent_name="port_agent")
```

类似的，当变量名称与 Agent 名称相同时，可以省略 `agent_name` 参数：

```python
self.port_agent = AgentPort()
```

## 监测方法接口匹配

为了与 Env 中的监测函数匹配，参考模型需要为每一个监测函数编写对应的 `MonitorPort`，定义方法与 `DriverPort` 一致。

```python
self.monitor_port = MonitorPort(agent_name="port_agent", monitor_name="monitor")

# 使用 "." 来指定监测函数的路径
self.monitor_port = MonitorPort("port_agent.monitor")

# 如果参考模型中的变量名称与监测函数名称相同，可以省略 monitor_name 参数
self.monitor = MonitorPort(agent_name="port_agent")

# 使用变量名称同时匹配 Agent 名称与监测函数名称，并使用 `__` 分隔
self.port_agent__monitor = MonitorPort()
```

MonitorPort 中送入的数据，将会自动与 Env 中的监测函数的返回值进行比较，来完成参考模型的比对工作。

当参考模型中定义的 `DriverPort`, `AgentPort` 和 `MonitorPort` 能够与 `Env` 中所有接口匹配时，参考模型便能成功与 `Env` 建立匹配关系，此时可以直接通过 `Env` 中的 `attach` 方法将参考模型附加到 `Env` 上。
