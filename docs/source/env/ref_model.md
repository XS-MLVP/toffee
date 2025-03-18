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
class AdderRefModel:
    def add(self, a, b):
        return a + b
```

在这个参考模型中，不需要任何内部状态，通过一个对外函数接口即可实现参考模型所有功能。

### 独立执行流模式

**独立执行流模式**即是将参考模型的行为定义为一个独立的执行流，它不再受外部主动调用函数控制，而拥有了主动获取输入数据的能力。当外部给参考模型发送数据时，参考模型不会立即响应，而是将这一数据保存起来，等待其执行逻辑主动获取该数据。

我们用一段代码来说明这种模式，该示例中用到了 toffee 中提供的相关概念来实现，但目前无需关心这些概念的使用细节。

```python
class AdderRefModel(Model):
    def __init__(self):
        super().__init__()

        self.add_port = DriverPort(agent_name="add_agent", driver_name="add")
        self.sum_port = MonitorPort(agent_name="add_agent", monitor_name="sum")

    async def main():
        while True:
            operands = await self.add_port()
            std_sum = operands["a"] + operands["b"]
            dut_sum = await self.sum_port()
            assert std_sum == dut_sum, f"Expected {std_sum}, but got {dut_sum}"
```

在这里，我们在参考模型构造函数中定义了两类接口，一类为**驱动接口(DriverPort)**，即代码中的`add_port`，用于接收测试代码通过驱动函数向DUT输入的数据；另一类为**监测接口(MonitorPort)**，即代码中的`sum_port`，用于接收监测函数监测到的DUT输出数据。 定义了这两个接口后，上层代码在给参考模型发送数据时，并不会触发参考模型中的某个函数，而是会将数据发送到 `add_port` 这个驱动接口中。同时，DUT的输出数据也会被发送到 `sum_port` 这个监测接口中。

那么参考模型如何去使用这两个接口呢？在参考模型中，有一个 main 函数，这是参考模型执行的入口，当参考模型创建时, main 函数会被自动调用，并在后台持续运行。在上面代码中 main 函数里，参考模型通过不断重复这一过程：等待 `add_port` 中的数据、计算结果、获取 `sum_port` 中的数据、比较结果，来完成参考模型的验证工作。

参考模型会主动向 `add_port` 请求数据，如果 `add_port` 中没有数据，参考模型会等待数据的到来。当数据到来后，参考模型将会进行计算，之后参考模型再次主动等待 `sum_port` 中的数据到来。它的执行过程是一个独立的执行流，不受外部的主动调用控制。当参考模型变得复杂时，其将会含有众多的驱动接口和监测接口，通过独立执行流的方式，可以更好的去处理结构之间的相互关系，尤其是接口之间存在调用顺序的情况。

## 如何编写函数调用模式的参考模型

### 驱动函数匹配

假如 Env 中定义的接口如下：

```plaintext
StackEnv
  - port_agent
    - @driver_method push
    - @driver_method pop
    - @monitor_method monitor_pop_data
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

toffee 还提供了以下几种匹配方式，以便更方便地匹配驱动函数：

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

### 监测函数匹配

Toffee 目前支持检测函数的匹配，通过 `@monitor_hook` 装饰器来表示这个函数是一个监测函数的匹配函数。与 `@driver_hook` 类似，为了建立对应关系，需要在装饰器中指定其对应的 Agent 和监测函数的名称，例如：

```python
class StackRefModel(Model):
    @monitor_hook(agent_name="port_agent", monitor_name="monitor_pop_data")
    def monitor_pop_data(self, item):
        pass
```

`monitor_hook` 含有一个固定的额外参数，例如上面代码中的 `item`，用于接收监测函数的返回值。当 Env 中的监测函数被调用时，参考模型中对应的 `monitor_hook` 函数将会被自动调用，在函数体的实现中可以判断监测函数的返回值是否符合预期。

`monitor_hook` 支持 `driver_method` 所支持的所有匹配方式。


### Agent 匹配

除了 `driver_hook` 与 `monitor_hook` 两种匹配单一函数的 Hook 之外，Toffee 还提供了 `agent_hook`，用于一次性匹配多个驱动函数或监测函数，方式如下：

```python
class StackRefModel(Model):
    @agent_hook("port_agent")
    def port_agent(self, name, item):
        pass
```

在这个例子中，`port_agent` 函数将会匹配 `port_agent` Agent 中的所有驱动函数与监测函数。当 Agent 中的任意一个驱动函数被调用时，`port_agent` 都会被自动调用，并将驱动函数的名称与参数传入。当 Agent 中的任意一个监测函数被调用时，`port_agent` 也会被自动调用，并将监测函数的名称与返回值传入。此外，如果 `agent_hook` 有返回值，框架会使用此函数的返回值与驱动函数的返回值进行对比。

与驱动函数类似，`@agent_hook` 装饰器也支持当函数名与 Agent 名称相同时省略 `agent_name` 参数。

```python
class StackRefModel(Model):
    @agent_hook()
    def port_agent(self, driver_name, args):
        pass
```

如果需要同时匹配多个 Agent，可以使用 `agent_hook` 中的 `agents` 参数，例如：

```python
class StackRefModel(Model):
    @agent_hook(agents=["port_agent", "port_agent2"])
    def port_agent(self, driver_name, args):
        pass
```

如果需要同时匹配多个驱动函数或监测函数，可以使用 `agent_hook` 中的 `methods` 参数，并指定需要匹配的驱动函数或监测函数的路径，例如：

```python
class StackRefModel(Model):
    @agent_hook(methods=["port_agent.push", "port_agent.pop", "port_agent2.monitor_pop_data"])
    def port_agent(self, driver_name, args):
        pass
```

> 与早期版本有差异
> `agent_hook` 目前可以独立使用，不需要与 `driver_hook` 相关。

### Hook 的调用顺序

对于以上三类 Hook，它们在同一周期的调用顺序均可以自行配置。默认情况下，`monitor_hook` 会在 `driver_hook` 和 `agent_hook` 之前被调用。如果需要调整这些 Hook 的调用顺序，可以通过 `priority` 参数来指定，数值越小，优先级越高，例如：

```python
class StackRefModel(Model):
    @driver_hook(agent_name="port_agent", driver_name="push", priority=1)
    def push(self, data):
        pass

    @monitor_hook(agent_name="port_agent", monitor_name="monitor_pop_data", priority=2)
    def monitor_pop_data(self, item):
        pass
```

对于 `driver_hook` 以及 `agent_hook` 来说，还可以指定其与 `driver_method` 之间的调用顺序。默认情况下，当 `driver_method` 被调用时，`driver_hook` 与 `agent_hook` 会在 `driver_method` 之前被调用。

如果需要调整其调用顺序，可以通过 `sche_order` 参数来指定：

- 当 `sche_order` 为 `model_first` 时 `driver_method` 会在 `driver_hook` 与 `agent_hook` 之前被调用
- 当 `sche_order` 为 `dut_first` 时，`driver_hook` 与 `agent_hook` 会在 `driver_method` 之后被调用

例如：

```python
class StackRefModel(Model):
    @driver_hook(agent_name="port_agent", driver_name="push", sche_order="dut_first")
    def push(self, data):
        pass
```

## 如何编写独立执行流模式的参考模型

独立执行流模式的参考模型是通过 `port` 接口的形式来得到外界的数据，可以主动从 `port` 中获取数据。在 toffee 中，我们提供了两种接口来实现这一功能，分别是 `DriverPort` 和 `MonitorPort`。

类似地，我们需要定义一系列的 `DriverPort` 使其与 Env 中的驱动函数匹配，同时定义一系列的 `MonitorPort` 使其与 Env 中的监测函数匹配。

当 Env 中的驱动函数被调用时，调用数据将会被发送到 `DriverPort` 中，参考模型将会主动获取这些数据，并进行计算。同时，当 Env 中的监测函数被调用时，返回数据将会被发送到 `MonitorPort` 中，参考模型将会主动获取这些数据，并进行比较。为了在接口中获取数据，需要调用 `DriverPort` 或 `MonitorPort` 实例，并通过 `await` 关键字来等待数据，例如：

> 与早期版本有差异
> `MonitorPort` 的调用更改为了从 `MonitorPort` 实例中获取数据。

```python
class StackRefModel(Model):
    def __init__(self):
        super().__init__()

        self.push_port = DriverPort(agent_name="port_agent", driver_name="push")
        self.pop_port = DriverPort(agent_name="port_agent", driver_name="pop")
        self.monitor_pop_data_port = MonitorPort(agent_name="port_agent", monitor_name="monitor_pop_data")

    async def main(self):
        while True:
            push_data = await self.push_port()
            pop_data = await self.pop_port()
            monitor_pop_data = await self.monitor_pop_data_port()
            # do something
```

### 驱动方法接口匹配

为了接收到 Env 中所有的驱动函数的调用，参考模型可以选择为需要获取驱动函数编写对应的 `DriverPort`。可以通过 `DriverPort` 的参数 `agent_name` 与 `driver_name` 来匹配 Env 中的驱动函数。

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

### 监测方法接口匹配

为了与 Env 中的监测函数匹配，参考模型需要为每一个监测函数编写对应的 `MonitorPort`，定义方法与 `DriverPort` 一致。

```python
class StackRefModel(Model):
    def __init__(self):
        super().__init__()

        self.monitor_pop_data_port = MonitorPort(agent_name="port_agent", monitor_name="monitor_pop_data")
```

类似的，`MonitorPort` 支持 `DriverPort` 中的所有匹配方式。

### Agent 接口匹配

也可以选择定义 `AgentPort` 同时匹配任意的驱动函数和监测函数，例如

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

同时，也可以使用 `agents` 参数来匹配多个 Agent，或使用 `methods` 参数来匹配多个驱动函数或监测函数。

使用了 `AgentPort` 之后，参考模型可以通过 `await` 关键字来等待 Env 中的驱动函数或监测函数的调用，其返回数据将会是包含了函数路径以及相关参数的元组。
