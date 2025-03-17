# 如何使用 Bundle

`Bundle` 在 toffee 验证环境中，用于构建 `Agent` 与 DUT 之间交互的中间层，以保证 `Agent` 与 DUT 之间的解耦。同时 `Bundle` 也起到了对 DUT 接口层次结构划分的作用，使得对 DUT 接口的访问变得更加清晰、方便。

## 一个简单的 Bundle 的定义

为了定义一个 `Bundle`，需要自定义一个新类，并继承 toffee 中的 `Bundle` 类。下面是一个简单的 `Bundle` 的定义示例：

```python
from toffee import Bundle, Signals

class AdderBundle(Bundle):
    a, b, sum, cin, cout = Signals(5)
```

该 Bundle 定义了一个简单的加法器接口，在 `AdderBundle` 类中，我们定义了五个信号 `a`, `b`, `sum`, `cin`, `cout`，这五个信号分别代表了加法器的输入端口 `a`, `b`，输出端口 `sum`，以及进位输入端口 `cin` 和进位输出端口 `cout`。

定义完成后，我们可以通过 `AdderBundle` 类的实例来访问这些信号，例如：

```python
adder_bundle = AdderBundle()

adder_bundle.a.value = 1
adder_bundle.b.value = 2
adder_bundle.cin.value = 0
print(adder_bundle.sum.value)
print(adder_bundle.cout.value)
```

## 将 DUT 绑定到 Bundle

在上述代码中，我们虽然创建了一个 bundle 实例，并对他进行了驱动，但是我们并没有将这个 bundle 与任何 DUT 绑定，也就意味着对这个 bundle 的操作，无法真正影响到 DUT。

使用 `bind` 方法，可以将一个 DUT 绑定到 bundle 上。例如我们有一个简单的加法器 DUT，其接口名称与 Bundle 中定义的名称相同。

```
adder = DUTAdder()

adder_bundle = AdderBundle()
adder_bundle.bind(adder)
```

`bind` 函数会自动检索 DUT 中所有的接口，并将名称相同的接口进行绑定。绑定完成后，对 bundle 的操作，就会直接影响到 DUT。

但是，如果 DUT 的接口名称与 Bundle 中定义的名称不同，直接使用 `bind` 则无法正确绑定。在 Bundle 中，我们提供多种绑定方法，以适应不同的绑定需求。

### 通过字典进行绑定

在 `bind` 函数中，我们可以通过传入一个字典，来指定 DUT 中的接口名称与 Bundle 中的接口名称之间的映射关系。

假设 Bundle 中的接口名称与 DUT 中的接口名称拥有如下对应关系：

```
a    -> a_in
b    -> b_in
sum  -> sum_out
cin  -> cin_in
cout -> cout_out
```

在实例化 `bundle` 时，我们可以通过 `from_dict` 方法创建，并传入一个字典，告知 `Bundle` 以字典的方式进行绑定。

```python
adder = DUTAdder()
adder_bundle = AdderBundle.from_dict({
    'a': 'a_in',
    'b': 'b_in',
    'sum': 'sum_out',
    'cin': 'cin_in',
    'cout': 'cout_out'
})
adder_bundle.bind(adder)
```

此时，`adder_bundle` 可正确绑定至 `adder`。

### 通过前缀进行绑定

假设 DUT 中的接口名称与 Bundle 中的接口名称拥有如下对应关系：

```
a    -> io_a
b    -> io_b
sum  -> io_sum
cin  -> io_cin
cout -> io_cout
```

可以发现，实际 DUT 的接口名称相比于 Bundle 中的接口名称，都多了一个 `io_` 的前缀。在这种情况下，我们可以通过 `from_prefix` 方法创建 `Bundle`，并传入前缀名称，告知 `Bundle` 以前缀的方式进行绑定。

```python
adder = DUTAdder()
adder_bundle = AdderBundle.from_prefix('io_')
adder_bundle.bind(adder)
```

### 通过正则表达式进行绑定

在某些情况下，DUT 中的接口名称与 Bundle 中的接口名称之间的对应关系并不是简单的前缀或者字典关系，而是更为复杂的规则。例如，DUT 中的接口名称与 Bundle 中的接口名称之间的对应关系为：

```
a    -> io_a_in
b    -> io_b_in
sum  -> io_sum_out
cin  -> io_cin_in
cout -> io_cout_out
```

在这种情况下，我们可以通过传入正则表达式，来告知 `Bundle` 以正则表达式的方式进行绑定。

```python
adder = DUTAdder()
adder_bundle = AdderBundle.from_regex(r'io_(.*)_.*')
adder_bundle.bind(adder)
```

使用正则表达式时，Bundle 会尝试将 DUT 中的接口名称与正则表达式进行匹配，匹配成功的接口，将会读取正则表达式中的所有捕获组，将其连接为一个字符串。再使用这个字符串与 Bundle 中的接口名称进行匹配。

例如对于上面代码中的正则表达式，`io_a_in` 会与正则表达式成功匹配，唯一的捕获组捕获到的内容为 `a`。`a` 这个名称与 Bundle 中的接口名称 `a` 匹配，因此 `io_a_in` 会被正确绑定至 `a`。

## 创建子 Bundle

很多时候，我们会需要一个 Bundle 包含一个或多个其他 Bundle 的情况，这时我们可以将其他已经定义好的 Bundle 作为当前 Bundle 的子 Bundle。

```python
from toffee import Bundle, Signal, Signals

class AdderBundle(Bundle):
    a, b, sum, cin, cout = Signals(5)

class MultiplierBundle(Bundle):
    a, b, product = Signals(3)

class ArithmeticBundle(Bundle):
    selector = Signal()

    adder = AdderBundle.from_prefix('add_')
    multiplier = MultiplierBundle.from_prefix('mul_')
```

在上面的代码中，我们定义了一个 `ArithmeticBundle`，它包含了自己的信号 `selector`。除此之外它还包含了一个 `AdderBundle` 和一个 `MultiplierBundle`，这两个子 Bundle 分别被命名为 `adder` 和 `multiplier`。

当我们需要访问 `ArithmeticBundle` 中的子 Bundle 时，可以通过 `.` 运算符来访问：

```python
arithmetic_bundle = ArithmeticBundle()

arithmetic_bundle.selector.value = 1
arithmetic_bundle.adder.a.value = 1
arithmetic_bundle.adder.b.value = 2
arithmetic_bundle.multiplier.a.value = 3
arithmetic_bundle.multiplier.b.value = 4
```

同时，当我们以这种定义方式进行定义后，在最顶层的 Bundle 进行绑定时，会同时将子 Bundle 也绑定到 DUT 上，在定义子 Bundle 时，依然可以使用前文提到的多种绑定方式。

需要注意的是，子 Bundle 的创建方法去匹配的信号名称，是经过上一次 Bundle 的创建方法进行处理过后的名称。例如在上面的代码中，我们将顶层 Bundle 的匹配方式设置为 `from_prefix('io_')`，那么在 `AdderBundle` 中去匹配的信号，是去除了 `io_` 前缀后的名称。

同时，字典匹配方法会将信号名称转换为字典映射后的名称传递给子 Bundle 进行匹配，正则表达式匹配方法会将正则表达式捕获到的名称传递给子 Bundle 进行匹配。

## Bundle 中的实用操作

### 信号访问与赋值

**访问信号值**

在 Bundle 中，我们不仅可以通过 `.` 运算符来访问 Bundle 中的信号，也可以通过 `[]` 运算符来访问 Bundle 中的信号。

```python
adder_bundle = AdderBundle()
adder_bundle['a'].value = 1
```

**访问未连接信号**

```python
def bind(self, dut, unconnected_signal_access=True)
```

在 `bind` 时，我们可以通过传入 `unconnected_signal_access` 参数来控制是否允许访问未连接的信号。默认为 `True`，即允许访问未连接的信号，此时当写入该信号时，该信号不会发生变化，当读取该信号时，会返回 `None`。 当 `unconnected_signal_access` 为 `False` 时，访问未连接的信号会抛出异常。

**同时赋值所有信号**

可以通过 `set_all` 方法同时将所有输入信号更改为某个值。

```python
adder_bundle.set_all(0)
```

**随机赋值所有信号**

可以通过 `randomize_all` 方法随机赋值所有信号。"value_range" 参数用于指定随机值的范围，"exclude_signals" 参数用于指定不需要随机赋值的信号，"random_func" 参数用于指定随机函数。

```python
adder_bundle.randomize_all()
```

**信号赋值模式更改**

信号赋值模式是 `picker` 中的概念，用于控制信号的赋值方式，请查阅 `picker` 文档以了解更多信息。

Bundle 中支持通过 `set_write_mode` 来改变整个 Bundle 的赋值模式。

同时，Bundle 提供了设置的快捷方法：`set_write_mode_as_imme`, `set_write_mode_as_rise` 与 `set_write_mode_as_fall`，分别用于设置 Bundle 的赋值模式为立即赋值、上升沿赋值与下降沿赋值。

### 消息支持

**默认消息类型赋值**

toffee 支持一个默认的消息类型，可以通过 `assign` 方法将一个字典赋值给 Bundle 中的信号。

```python
adder_bundle.assign({
    'a': 1,
    'b': 2,
    'cin': 0
})
```

Bundle 将会自动将字典中的值赋值给对应的信号，当需要将未指定的信号赋值成某个默认值时，可以通过 `*` 来指定默认值：

```python
adder_bundle.assign({
    '*': 0,
    'a': 1,
})
```

**子 Bundle 的默认消息赋值支持**

如果希望通过默认消息类型同时赋值子 Bundle 中的信号，可以通过两种方式实现。当 `assign` 中的 `multilevel` 参数为 `True` 时，Bundle 支持多级字典赋值。

```python
arithmetic_bundle.assign({
    'selector': 1,
    'adder': {
        '*': 0,
        'cin': 0
    },
    'multiplier': {
        'a': 3,
        'b': 4
    }
}, multilevel=True)
```

当 `multilevel` 为 `False` 时，Bundle 支持通过 `.` 来指定子 Bundle 的赋值。

```python
arithmetic_bundle.assign({
    '*': 0,
    'selector': 1,
    'adder.cin': 0,
    'multiplier.a': 3,
    'multiplier.b': 4
}, multilevel=False)
```

**默认消息类型读取**

在 Bundle 中可以使用，`as_dict` 方法将 Bundle 当前的信号值转换为字典。其同样支持两种格式，当 `multilevel` 为 `True` 时，返回多级字典；当 `multilevel` 为 `False` 时，返回扁平化的字典。

```python
> arithmetic_bundle.as_dict(multilevel=True)
{
    'selector': 1,
    'adder': {
        'a': 0,
        'b': 0,
        'sum': 0,
        'cin': 0,
        'cout': 0
    },
    'multiplier': {
        'a': 0,
        'b': 0,
        'product': 0
    }
}
```

```python
> arithmetic_bundle.as_dict(multilevel=False)
{
    'selector': 1,
    'adder.a': 0,
    'adder.b': 0,
    'adder.sum': 0,
    'adder.cin': 0,
    'adder.cout': 0,
    'multiplier.a': 0,
    'multiplier.b': 0,
    'multiplier.product': 0
}
```

**自定义消息类型**

在我们自定义的消息结构中，可以执行规则将其赋值给 Bundle 中的信号。

一种方法是，在自定义消息结构中，实现 `as_dict` 函数，将自定义消息结构转换为字典，然后通过 `assign` 方法赋值给 Bundle。

另一种方法是，在自定义消息结构中，实现 `__bundle_assign__` 函数，其接收一个 Bundle 实例，将自定义消息结构赋值给 Bundle。实现后，可以通过 `assign` 方法赋值给 Bundle，Bundle 将会自动调用 `__bundle_assign__` 函数进行赋值。

```python
class MyMessage:
    def __init__(self):
        self.a = 0
        self.b = 0
        self.cin = 0

    def __bundle_assign__(self, bundle):
        bundle.a.value = self.a
        bundle.b.value = self.b
        bundle.cin.value = self.cin

my_message = MyMessage()
adder_bundle.assign(my_message)
```

当需要将 Bundle 中的信号值转换为自定义消息结构时，简易在自定义消息结构中实现 `from_bundle` 的类方法，接收一个 Bundle 实例，返回一个自定义消息结构。在创建自定义消息结构时，可以通过 `from_bundle` 方法将 Bundle 中的信号值转换为自定义消息结构。

```python
class MyMessage:
    def __init__(self):
        self.a = 0
        self.b = 0
        self.cin = 0

    @classmethod
    def from_bundle(cls, bundle):
        message = cls()
        message.a = bundle.a.value
        message.b = bundle.b.value
        message.cin = bundle.cin.value
        return message

my_message = MyMessage.from_bundle(adder_bundle)
```

### 时序封装

Bundle 类除了对 DUT 的引脚进行封装外，还提供了基于数组的时序封装，可以适用于部分简单时序场景。Bundle 类提供了`process_requests(data_list)`函数，他接受一个数组输入，第`i`个时钟周期，会将`data_list[i]`对应的数据赋值给引脚。`data_list`中的数据可以是`dict`类型，或者`callable(cycle, bundle_ins)`类型的可调用对象。对于`dict`类型，特殊`key`有：

```bash
__funcs__: func(cycle, self)  # 可调用对象，可以为函数数组[f1,f2,..]
__condition_func__:  func(cycle, slef, cargs) # 条件函数，当改函数返回为true时，进行赋值，否则继续推进时钟
__condition_args__:  cargs # 条件函数需要的参数
__return_bundles__:  bundle # 需要本次dict赋值时返回的bundle数据，可以是list[bundle]
```

如果输入的`dict`中有`__return_bundles__`，则函数会返回该输入对应的 bundle 值，例如`{"data": x, "cycle": y}`。以 Adder 为例，期望第三次加后返回结果：

```python
# Adder虽然为存组合逻辑，但此处当时序逻辑使用
class AdderBundle(Bundle):
    a, b, sum, cin, cout = Signals(5)             # 指定引脚

    def __init__(self, dut):
        super().__init__()
        # init clock
        # dut.InitClock("clock")
        self.bind(dut)                            # 绑定到dut

    def add_list(data_list =[(1,2),(3,4),(5,6),(7,8)]):
        # make input dit
        data = []
        for i, (a, b) in enumerate(data_list):
            x = {"a":a, "b":b, "*":0}             # 构建budle赋值的dict
            if i >= 2:
                x["__return_bundles__"] = self    # 设置需要返回的bundle
                data.append(X)
        return self.process_requests(data)        # 推动时钟，赋值，返回结果
```

当调用`add_list()`后，返回的结果为:

```python
[
  {"data": {"a":5, "b":6, "cin": 0, "sum":11, "cout": 0}, "cycle":3},
  {"data": {"a":7, "b":8, "cin": 0, "sum":15, "cout": 0}, "cycle":4}
]
```

### 异步支持

在 Bundle 中，为了方便的接收时钟信息，提供了 `step` 函数。当 Bundle 连接至 DUT 的任意一个信号时，step 函数会自动同步至 DUT 的时钟信号。

可以通过 `step` 函数来完成时钟周期的等待。

```python
async def adder_process(adder_bundle):
    adder_bundle.a.value = 1
    adder_bundle.b.value = 2
    adder_bundle.cin.value = 0
    await adder_bundle.step()
    print(adder_bundle.sum.value)
    print(adder_bundle.cout.value)
```

### 信号连接

**信号连接规则**

当定义好 Bundle 实例后，可以调用 `all_signals_rule` 方法，获取所有信号的连接规则，以帮助用户检查信号的连接规则是否符合预期。

```python
adder_bundle.all_signals_rule()
```

**信号可连接性检查**

`detect_connectivity` 方法可以检查一个特定的信号名称是否可以连接到该 Bundle 中的某个信号。

```python
adder_bundle.detect_connectivity('io_a')
```

`detect_specific_connectivity` 方法可以检查一个特定的信号名称是否可以连接到该 Bundle 中的某个特定的信号。

```python
adder_bundle.detect_specific_connectivity('io_a', 'a')
```

如果需要检测子 Bundle 的信号连接性，可以通过 `.` 运算符来指定。

### DUT 信号连接检查

**未连接信号检查**

`detect_unconnected_signals` 方法可以检查 DUT 中未连接到任何 Bundle 的信号。

```python
Bundle.detect_unconnected_signals(adder)
```

**重复连接检查**

`detect_multiple_connections` 方法可以检查 DUT 中同时连接到多个 Bundle 的信号。

```python
Bundle.detect_multiple_connections(adder)
```

### 其他实用操作

**设置 Bundle 名称**

可以通过 `set_name` 方法设置 Bundle 的名称。

```python
adder_bundle.set_name('adder')
```

设置名称之后，将会得到更加直观的提示信息。

**获取 Bundle 中所有信号**

`all_signals` 信号会返回一个 generator，其中包含了包括子 Bundle 信号在内的所有信号。

```python
for signal in adder_bundle.all_signals():
    print(signal)
```

## Bundle 的自动生成脚本

在很多情况下，DUT 的接口可能过于复杂，手动去编写 Bundle 的定义会变得非常繁琐。然而，Bundle 作为中间层，提供一个确切的信号名称定义是必要的。为了解决这个问题，toffee 提供了一个自动生成 Bundle 的脚本来从 DUT 的接口定义中生成 Bundle 的定义。

可以在 toffee 仓库目录下的 `scripts` 文件夹中找到 `bundle_code_gen.py` 脚本。该脚本可以通过解析 DUT 实例，以及指定的绑定规则自动生成 Bundle 的定义。

其中提供了三个函数

```python
def gen_bundle_code_from_dict(bundle_name: str, dut, dict: dict, max_width: int = 120)
def gen_bundle_code_from_prefix(bundle_name: str, dut, prefix: str = "", max_width: int = 120):
def gen_bundle_code_from_regex(bundle_name: str, dut, regex: str, max_width: int = 120):
```

分别用于通过字典、前缀、正则表达式的方式生成 Bundle 的定义。

使用时，指定 Bundle 的名称，DUT 实例，以及对应的生成规则便可生成 Bundle 的定义，还可以通过 `max_width` 参数来指定生成的代码的最大宽度。

```python
from bundle_code_gen import *

gen_bundle_code_from_dict('AdderBundle', dut, {
    'a': 'io_a',
    'b': 'io_b',
    'sum': 'io_sum',
    'cin': 'io_cin',
    'cout': 'io_cout'
})
gen_bundle_code_from_prefix('AdderBundle', dut, 'io_')
gen_bundle_code_from_regex('AdderBundle', dut, r'io_(.*)')
```

生成好的代码可以直接或经过简单的修改后，复制到代码中使用。也可以作为子 Bundle 的定义，应用到其他 Bundle 中。
