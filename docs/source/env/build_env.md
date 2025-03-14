# 如何搭建 Env

`Env` 在 toffee 验证环境中用于打包整个验证环境，Env 中直接实例化了验证环境中需要用的所有 agent，并负责将这些 Agent 需要的 bundle 传递给它们。

创建好 Env 后，参考模型的编写规范也随之确定，按照此规范编写的参考模型可直接附加到 Env 上，由 Env 来完成参考模型的自动同步。

## 创建 Env

为了定义一个 `Env`，需要自定义一个新类，并继承 toffee 中的 `Env` 类。下面是一个简单的 `Env` 的定义示例：

```python
from toffee.env import *

class DualPortStackEnv(Env):
    def __init__(self, port1_bundle, port2_bundle):
        super().__init__()

        self.port1_agent = StackAgent(port1_bundle)
        self.port2_agent = StackAgent(port2_bundle)
```

在这个例子中，我们定义了一个 `DualPortStackEnv` 类，该类中实例化了两个相同的 `StackAgent`，分别用于驱动两个不同的 `Bundle`。

可以选择在 Env 之外连接 Bundle，也可以在 Env 内部连接 Bundle，只要能保证向 Agent 中传入正确的 Bundle 即可。

此时，如果不需要编写额外的参考模型，那么整个验证环境的搭建就完成了，可以直接编写测试用例并且在测试用例中使用 Env 提供的接口，例如：

```python
port1_bundle = StackPortBundle()
port2_bundle = StackPortBundle()
env = DualPortStackEnv(port1_bundle, port2_bundle)

await env.port1_agent.push(1)
await env.port2_agent.push(1)
print(await env.port1_agent.pop())
print(await env.port2_agent.pop())
```

## 附加参考模型

定义好 Env 后，整个验证环境的接口也就随之确定，例如：

```
DualPortStackEnv
  - port1_agent
    - @driver_method push
    - @driver_method pop
    - @monitor_method some_monitor
  - port2_agent
    - @driver_method push
    - @driver_method pop
    - @monitor_method some_monitor
```

按照此规范编写的参考模型都可以直接附加到 Env 上，由 Env 来完成参考模型的自动同步，方式如下：

```python
env = DualPortStackEnv(port1_bundle, port2_bundle)
env.attach(StackRefModel())
```

一个 Env 可以附加多个参考模型，这些参考模型都将会被 Env 自动同步。

参考模型的具体编写方式将在编写参考模型一节中详细介绍。
