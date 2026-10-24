# 迁移到 Toffee 2

Toffee 2 基于 XReactor 1.1.0 的实现迁入，提供驱动、监视、参考模型、结果检查和覆盖率。
**Toffee Legacy（旧版）的实现与提交历史保留在
[`master`](https://github.com/XS-MLVP/toffee/tree/master)；新实现位于 `main`。**
旧版安装包名为 `pytoffee`，新版为 `uc-toffee`；Python 导入名都为 `toffee`，但 API 与运行语义不兼容。
两代实现应放在不同的虚拟环境中。
旧验证环境需要按本文调整运行作用域、信号绑定、模型接线和采样语义。

本次迁移基线为 [XReactor 1.1.0 `3017293`](https://github.com/XS-MLVP/xreactor/tree/3017293a7597c39adbae883e15d63d727bcbf6ae)
和 [旧 Toffee `f57dd6f`](https://github.com/XS-MLVP/toffee/tree/f57dd6ffc295b9b8bad3e3b3ce040b8a849f5c91)。
旧版本实际发布在 `0.x` 系列，本文使用“旧版”指代，不将其称为已发布的 1.x。
Toffee 2 从 `2.0.0` 开始以 `uc-toffee` 分发，可用
`python3 -m pip install "uc-toffee[runner]"` 安装。

## 旧项目如何选择

继续使用 Legacy 的项目保留现有依赖锁定；从 Git 安装时明确指定 `master` 或已经验证的旧提交，
避免跟随默认分支切换。旧 `0.x` 标签继续指向原实现。

迁移到 Toffee 2 时：

1. 建立独立的 Python 3.11+ 环境，运行 `python3 -m pip install "uc-toffee[runner]"`。
2. 按下表迁移测试入口、Bundle、Driver、Monitor 与参考模型接线。
3. 根据采样和条件等待章节检查周期边界、事务完成条件与清理行为。
4. 运行项目已有回归，验证结果后再更新项目依赖。

两代实现提供相同的 `import toffee`，应在独立环境中进行迁移与对比。

## 从 XReactor 1.1 迁入

Python 导入由 `xreactor` 改为 `toffee`，命令由 `xreactor`、`xreactor-coverage-report`
改为 `toffee`、`toffee-coverage-report`；分发包名为 `uc-toffee`。
底层公开类 `XReactor` 更名为 `Reactor`。项目中的 `pytest_xreactor_coverage` hook、
`xreactor_register_dut` fixture 和 `xreactor_functional_coverage` marker 分别改用 `toffee` 前缀。
框架自带的 runner 继续使用 pytest、pytest-asyncio 和 pytest-xdist，无需 `toffee-test`。

覆盖率 JSON 的 `xreactor-*` 格式标识及已有报告目录标记保持兼容，既有报告仍可读取。
原生依赖的固定提交、ABI 与覆盖率协议不随此次命名迁移改变。

## 两个版本的主要区别

| 方面 | 旧 Toffee（Legacy） | Toffee 2 |
| --- | --- | --- |
| 仿真推进 | Python 时钟任务逐次调用 `dut.Step(1)`，扫描宿主 loop 中的任务，等待可运行协程处理完 | `Execution` 观察本次仿真的即时回调；原生后端 批量推进，在事件或阶段屏障处返回 |
| 条件等待 | `Value`、`Condition` 等在 Python 中随周期唤醒并检查 | 可编译的条件、Sequence 和 FSM 在 C++ 求值；Python 条件函数 显式使用 `@pytrigger` |
| 采样时刻 | 主要跟随 DUT、Bundle 或 pin 的周期 event | 显式指定上升沿、下降沿或驱动稳定阶段；条件使用 `sample` 或 `default_sample` |
| 等待结果 | Trigger 是 coroutine，多数等待只表示完成 | Trigger 是可复用规格，每次 await 独立注册，并返回包含 tick、phase 等信息的 `XEvent` |
| 信号组织 | `Bundle` 子类声明 `Signal`、`Signals`，按前缀、正则或字典绑定 | `Bundle` 组合 live signal，支持显式字段、signal tree 和 packed 视图；`sample` 返回不可变快照 |
| Agent | 在 Agent 内声明多个 `@driver_method` 和 `@monitor_method` | Agent 组合一个可选 Driver 和多个具名 Monitor，封装提交及响应检查 |
| 参考模型 | `Model` 的 Hook 或 Port 通过 `Env.attach` 按路径匹配接线 | 普通 Python 模型通过 `agent.connect(model)` 接线，自动响应链在实际接受后调用 `accept` |
| 检查方式 | 驱动方法返回值与 Hook 结果比较，或通过监视器及模型端口检查 | 独立 `Scoreboard` 支持直接比较、FIFO、key 和固定延迟关联，以及周期预算与结束检查 |
| 并发组织 | `Executor` 提供调度组、优先级和 model first 等顺序设置 | Driver 管理输入资源与提交；场景使用普通 async 函数，独立并发使用 asyncio |
| 生命周期 | 监视器在构造时创建任务；标准测试结束路径会取消 loop 中其他任务 | `Execution` 启停注册的 Agent 并清理框架资源；独立用户任务由创建者管理 |
| 功能覆盖 | `CovGroup` 绑定对象与 Python 条件，可标记相关函数 | 定义与运行实例分离，提供 bins、cross、转移、原生采集、带来源的保存与合并 |
| 测试入口 | `toffee.run`，以及独立的 `toffee-test` pytest 插件 | 标准 pytest 与 pytest-asyncio；运行产物由项目 fixture 或运行配方保存 |

旧版的调度和条件实现见
[asynchronous.py](https://github.com/XS-MLVP/toffee/blob/f57dd6ffc295b9b8bad3e3b3ce040b8a849f5c91/toffee/asynchronous.py)、
[triggers.py](https://github.com/XS-MLVP/toffee/blob/f57dd6ffc295b9b8bad3e3b3ce040b8a849f5c91/toffee/triggers.py)。
新版的职责划分见[Execution](execution-and-backend.md)和[Agent 与参考模型](agents-and-reference-models.md)。

原生批量等待减少了未命中周期中的 Python 调度和跨语言调用。如果 Driver、Monitor
或 Python 条件函数 仍需每周期执行 Python，收益会受到这些工作的限制。
比较迁移前后的性能时，应保持输入、采样范围和仿真选项一致，直接测量真实项目。
[性能测试说明](https://github.com/XS-MLVP/toffee/blob/main/benchmarks/README.md)中的空时钟测试只能比较框架开销，
不能直接换算成真实 DUT 的加速倍数。

## 安装与运行作用域

旧版需要 Python 3.8 或更新版本；新版的最低版本是 Python 3.11。在 toffee 仓库根目录
安装框架和测试依赖：

```bash
python3 -m pip install -e '.[test]'
```

真实 DUT 需要带 `XTriggerEngine`、half-step 和 stable sampling 能力的 xcomm/xspcomm
绑定。已有 Picker DUT 是否可以继续使用，取决于它暴露的 XClock、XData 和底层
绑定 能力；仅安装 Python 包不能补齐旧原生绑定。接入方法见
[native 与项目 DUT](../getting-started/native-and-next.md)。

旧版的 `start_clock(dut)` 加 `toffee.run(...)` 改为在已有 asyncio loop 内进入
`Execution`。下面的 `dut` 由项目创建，`dut.clock` 是已注册的时钟 pin：

```python
from toffee import ClockCycles, Execution, RisingEdge, XCommClockBackend

backend = XCommClockBackend(dut.GetXClock())
try:
    async with Execution(backend, default_sample=RisingEdge(dut.clock)):
        await ClockCycles(dut.clock, 1)
finally:
    backend.close()
```

`Execution` 退出会清理本次注册和组件，但保留 Backend 与 DUT 状态。
创建 Backend 的 fixture 负责关闭它，创建 DUT 的 fixture 按生成包接口释放 DUT。
跨用例复用 Backend 时须明确 reset；已关闭的 Agent 和内置 Monitor 需要重新创建。
运行中不要同时用 `dut.Step`、旧版 时钟任务和 Execution 推进同一个 DUT。

## 常用 API 对应关系

下表中的对应关系说明改写方向；相同名称也需要核对下一节的语义。

| 旧版 用法 | 新版 用法与注意事项 |
| --- | --- |
| `toffee.run(test)`、`start_clock(dut)` | 普通 asyncio 入口或 pytest-asyncio，加 `async with Execution(backend)` |
| `await bundle.step(n)`、`await ClockCycles(dut, n)` | `await ClockCycles(clock, n)`；显式传 XClock 或其已注册时钟 pin，n 必须为正整数 |
| `await Value(pin, value, delay=...)` | `await Value(signal, value, sample=...)`；旧 delay 需要单独处理，见下节 |
| `await Condition(item, predicate)` | 可编译表达式用 `@xtrigger`；任意同步 Python 判断用 `@pytrigger` 或 `PythonPredicateTrigger` |
| `await AllValid(*pins)` | 显式定义多个信号同时有效的 `@xtrigger` 条件 |
| `await Change(pin)` | `await ValueChange(signal, sample=...)` |
| `Delayer(signal, delay)` | 在明确采样阶段保存信号历史；检查固定延迟响应时使用 Scoreboard 的延迟关联 |
| `RisingEdge(pin)`、`FallingEdge(pin)` | 新版 同名 API 等待时钟 stable phase；任意数据信号的采样边沿须另行表达 |
| `Event`、`Queue`、`sleep`、`create_task`、`gather` | 使用原生 asyncio 对象；与 DUT Trigger 组合时使用外部事件适配器 |
| `Signal`、`Signals`、`Bundle` 子类声明 | `Bundle(...)`、`Bundle.bind(..., Field(...))` 或 `Bundle.bind_tree(...)` |
| `bundle.assign(...)` | 完整结构用 `bundle.drive(...)`；部分字段更新显式写叶子信号 |
| `bundle.as_dict()` 读取当前值 | `bundle.sample()` 得到快照；需要字典时用快照的 `as_dict()` |
| `@driver_method` | 项目 Driver 的驱动实现，加 Agent 的 `send` 或 `submit`；协议便捷方法可由项目封装 |
| `@monitor_method` | 显式采样 Trigger 加同步 capture，通常使用 `SamplingMonitor` |
| `Env.attach(Model())`、`@driver_hook` | 环境显式构造 Agent 和普通模型，并调用 `agent.connect(model)` |
| `@monitor_hook`、模型 Port | 在环境中显式消费观察记录、调用模型和检查器；没有同名自动接线接口 |
| `CovGroup`、`add_watch_point` | `CoverGroupDef`、`CoverPointDef`、`Bin`，再实例化与采样 |
| `@toffee_test.testcase`、`@toffee_test.fixture` | `@pytest.mark.asyncio`、`@pytest.fixture` 或 `@pytest_asyncio.fixture` |

## 采样与条件等待的语义变化

### 显式选择时钟与阶段

旧版的 `RisingEdge(pin)` 和 `FallingEdge(pin)` 比较逐周期读取的 pin 值。
新版的同名 Trigger 表示 XClock 的稳定半周期屏障，原生后端 要求来源是自己的
XClock 或已注册时钟 pin。旧代码中的 `RisingEdge(dut.irq)` 不能直接沿用；若要识别
按时钟采样的单 bit irq 从 0 到 1，可以使用记录前值的 Python 条件函数 或时序模式。
它们识别指定采样点之间的变化，不能捕获任意采样间隔内的毛刺。

普通用例用 `ClockCycles` 表达周期等待；在上升沿接受输入的协议可以由 Driver
封装“下降沿写入，下一上升沿接受”。`DriveStable` 用于下降沿写入后的组合逻辑稳定
采样，具体契约见[Trigger 指南](triggers.md)。

### 旧 delay 参数需要核对边界

旧版的 `Value(pin, value, delay=1)` 至少等待一个周期 event 再检查；`delay=0` 可以
立即检查当前值。新版的 `Value` 从注册后的下一个明确采样点开始求值，即使条件当前
已经为真也不会立即返回。

旧 `delay=N` 若要求从第 N 个上升沿开始检查，N 大于 1 时可以先等待 N 减 1 个
上升沿，再注册 Value。先等待 N 个上升沿再注册，会把最早检查推迟到第 N 加 1 个。
这个对应关系要求旧周期 event 与选定的上升沿采样具有相同含义。

```python
# 在已有 Execution 内，从第三个后续上升沿开始判断。
await ClockCycles(clock, 2)
event = await Value(dut.ready, 1, sample=RisingEdge(clock))
```

旧 `delay=0` 的立即检查由项目显式读取当前稳定值并决定是否继续等待。
单次 Value 默认 `mode="enter"`，新注册的前值为 false；持久监视每个有效周期时，
必须选 `mode="each_sample"`，否则连续多个周期保持 valid=1 只会产生一次进入事件。

### 编译条件与 Python 条件分开

旧版的 `Condition` 每周期执行 Python 函数。能表达为信号条件的逻辑可以改为：

```python
from toffee import RisingEdge, xtrigger

@xtrigger(sample=RisingEdge("clock"))
def accepted(dut):
    return (dut.valid != 0) & (dut.ready != 0)

# 在 Execution 内调用：event = await accepted(dut)
```

布尔组合使用 `&`、`|`、`~`。不支持的 Python 运算会报错，不会自动回退。
查表、复杂对象计算等逻辑显式用 `@pytrigger`，保留逐采样执行 Python 的成本。
Sequence 和 FSM 用于识别模式；窗口失配不会自动使测试失败，需要另外设置失败期限。

### 等待结果与超时需要处理

`await trigger` 返回 `XEvent`，其 tick、phase 和事件值描述命中时刻。
在异步处理或组合等待之后，live signal 可能已经变化，相关数据应由 Monitor 的
同步 capture 保存。

`SimTimeout` 和 `WallTimeout` 到期返回 TIMEOUT 事件，调用方须显式抛出异常才能让
测试失败。`AnyOf` 是通用竞争等待；严格事务截止周期由 Scoreboard 的响应预算检查，
详见[超时语义](triggers.md)和[Scoreboard](scoreboard.md)。

## Bundle 与数据迁移

旧版 先声明 Bundle 子类，再通过 `from_prefix`、正则或字典绑定。
新版 直接组织实际信号，或者用显式字段映射检查名字和位宽：

```python
from toffee import Bundle, Field

request = Bundle.bind(dut, {
    "address": Field("io_req_addr", width=32),
    "data": Field("io_req_data", width=64),
})
request.drive({"address": 0x8000, "data": 3})
snapshot = request.sample()
assert snapshot.data.as_int() == 3
```

`Bundle.drive` 要求结构完整且没有多余字段。旧 assign 的部分更新、通配符和
`__bundle_assign__` 自定义行为，需要改为项目自己的编码或显式叶子写入。
`Bundle.bind_tree` 需要项目提供 signal tree；没有 metadata 的 DUT 可以先显式绑定。
不要把旧前缀或正则绑定代码直接套到同名 Bundle 上。

快照叶子为保存位宽和 X/Z 的 `LogicValue`，`as_int()` 遇到未知位会报错。
模型中的整数算法可以保留，边界处需要显式转换，并定义未知输入如何处理。
原生条件当前使用无符号语义，负常量、隐式截断和 signed cast 不能直接沿用。
packed 数组和结构的迁移见[数据指南](data.md)。

## Driver Monitor 与参考模型迁移

旧版的 Agent 同时容纳驱动和监视方法，Env 根据路径把 Hook 和 Port 注入这些方法。
新版 将驱动、采样、预测和比较分开，Agent 负责把接口所需的组件接在一起。
一个 旧版 Agent 的多个操作可以编码为不同请求类型，由同一个 Driver 执行；需要独立
输入资源的接口可以分别构造 Agent。

旧版的 driver Hook 可以选择 `model_first` 或 `dut_first`，并设置优先级。
新版的自动响应链在 Driver 报告**实际接受输入之后**调用同步 `model.accept(request)`，
再保存期望并关联响应。状态更新依据接受顺序；排队但尚未接受的请求不能提前更新模型。
旧模型中的功能计算通常可以保留，Hook 装饰器、路径匹配和调度设置需要重写。

模型 `accept` 返回本笔期望响应，必须同步执行并返回独立结果。
显式传入 expected 会覆盖比较值，但已连接模型仍会更新状态。旧版的 Hook 返回
`None` 时跳过结果比较；新版 中 `None` 是合法期望值，不能用它表示“本笔没有响应”。

### 一个可运行的迁移后环境

下面的文件使用仓库内的教学 DUT：每个上升沿 enable=1 时接受 operand，8 bit
累加结果两拍后产生一次响应。没有 ready 背压，因此单周期 Driver 足够。
真实协议需要把接受条件和输入空闲行为写入自己的 Driver。
将代码保存为仓库根目录的 `test_toffee_新版_example.py` 后运行 pytest 即可：

```python
import pytest
import pytest_asyncio

from examples.getting_started.toy_dut import TutorialDut
from toffee import (
    Agent, Bundle, Execution, PythonPredicateTrigger, RisingEdge,
    SamplingMonitor, SyncSingleCycleDriver,
)


class AccumulatorModel:
    def __init__(self):
        self.total = 0

    def accept(self, operand):
        self.total = (self.total + operand) % 256
        return self.total


@pytest_asyncio.fixture
async def accumulator():
    dut = TutorialDut(latency=2)
    driver = SyncSingleCycleDriver(
        dut.clock,
        Bundle(enable=dut.enable, operand=dut.operand),
        idle={"enable": 0, "operand": 0},
        encoder=lambda operand: {"enable": 1, "operand": operand},
    )
    monitor = SamplingMonitor(
        PythonPredicateTrigger(
            "response-present", lambda: dut.response is not None,
            sample=RisingEdge(dut.clock), mode="each_sample",
        ),
        capture=lambda event: dut.response,
    )
    agent = Agent(
        "accumulator", driver=driver, monitors={"response": monitor},
        response_monitor="response", clock=dut.clock,
        response_timeout_cycles=3,
    )
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            yield agent
    finally:
        dut.close()


@pytest.mark.asyncio
async def test_accumulator(accumulator):
    for operand in [250, 10]:
        await accumulator.send(operand)
    await accumulator.finish(timeout_cycles=10, observe_cycles=1)
```

```bash
python3 -m pytest -q test_toffee_新版_example.py
```

此例使用 Python 条件函数 判断教学 DUT 的 Python 输出；真实 XData 条件可以使用
编译 Trigger。环境中不需要 `Model` 基类、Hook 装饰器或 `Env.attach`。

旧版的 Monitor 接收队列满时会记录错误并丢弃新记录；新版的 Monitor 和订阅默认使用
有界 lossless 交付，溢出会传播为验证失败。只有明确允许丢弃旧数据的遥测场景，才选择
`delivery="latest"`。响应检查需要保留每一笔记录。

### send 与 submit 等待不同的终点

| 配置和调用 | 调用方等待什么 |
| --- | --- |
| `await agent.send(request)` | 输入接受；已配置响应检查时，检查器继续在后台核对响应 |
| AsyncDriver 加响应检查时，`transfer = agent.submit(request)` | 调用时立即提交；`await transfer` 等待关联和比较后的响应 |
| `await agent.drain(timeout_cycles=...)` | 排空调用前已提交的事务，允许之后继续提交 |
| `await agent.finish(timeout_cycles=..., observe_cycles=...)` | 停止新提交、排空、观察尾部窗口并检查残余；Monitor 仍由 Execution 关闭 |

旧版 驱动方法返回 DUT 结果的代码，迁移后如果需要响应值，应使用支持响应检查的
AsyncDriver Agent 的 submit，或项目明确的响应接收路径。
SyncDriver Agent 不提供 submit。AsyncDriver 的 send 返回 awaitable 句柄，不能直接
交给要求 coroutine 的 `asyncio.create_task`；保存句柄后直接 await 即可。

旧测试体返回不等于响应已经检查完。成功路径应在结束提交后执行有预算的 finish，
为 DUT 延迟和额外响应选择合理观察窗口。无人 await 的后台检查失败仍会从 finish
或 Execution 退出传播。

取消等待句柄的协程不撤销底层异步事务；显式 cancel 只允许撤销尚未接受的请求。
已接受输入的 reset、flush 和旧响应处置由协议环境明确实现。

### 被动模型与复杂响应需要显式接线

自动 `connect` 响应链当前是一笔主动接受请求对应一笔响应。旧版的 AgentPort、
DriverPort、MonitorPort，以及多模型、多接口 Hook 没有逐项兼容接口。
被动输入、多响应、DUT 自主发起事务等场景，使用输入和输出 Monitor 的观察记录，
由环境定义模型更新和 Scoreboard 关联规则。

多个 Agent 可以共享同一模型，但框架不推断同 tick 的跨接口仲裁和更新顺序。
旧 priority 和 model first 设置若表达了硬件顺序，迁移时须将该规则写在项目环境中。
检查器独占消费的响应 Monitor 不能同时交给用例 recv；Queue 的多个消费者会分流，
需要广播时由项目显式分发不可变观察记录。

## Executor 与外部异步任务迁移

新版 没有 旧版 Executor 的调度组、priority、sche_order 和退出策略的直接替代类。
同一输入接口的排队由 AsyncDriver 管理；普通激励序列用 async 函数串行执行。
独立接口的场景可以用 `asyncio.TaskGroup` 并发，项目负责表达原先调度组之间的顺序。

新版使用原生 asyncio Task、Lock、Queue、Event 等对象。
`XEvent` 是已发生的仿真结果；旧 `toffee.Event()` 应改为 `asyncio.Event()`。
需要与 DUT 等待组合时使用 `AsyncioEventTrigger`、`QueueTrigger` 或 `TaskComplete`。

长期后台服务可以通过 `execution.external_task(coro)` 放到仿真域外，创建者负责
结束任务。域内无限 `sleep(0)` 循环会阻止阶段收敛，并可能触发
`SimulationNotSettledError`。域外任务不能直接 await DUT Trigger，应把结果交给
仿真任务使用。

等待 HTTP 或其他外部 I/O 不会自动暂停时钟；只要仍有仿真 watcher，仿真就可以
继续推进。需要配置期间冻结时钟时，用 `execution.paused()`，退出暂停后再等待
依赖时钟推进的 Trigger。详见[asyncio 与 pytest](asyncio-and-pytest.md)。

## 功能覆盖与测试报告迁移

旧版的 CovGroup 将 live 对象和 Python 条件放在一起；新版 将静态覆盖定义、运行实例
和采样分开。基本对应关系如下：

| 旧版 | 新版 |
| --- | --- |
| `Eq(value)`、`In(values)` | `Bin.values(...)` |
| `IsInRange(low, high)` | `Bin.range(low, high)` |
| `add_watch_point(target, bins, name)` | `CoverPointDef(name, bins)`，采样时提供字段或绑定 XData |
| Python 条件函数 bin、dynamic bin | 将计算结果作为事务字段，定义稳定 bins；没有任意 callable bin 的同名替代 |
| `is_all_covered()` | 按目标使用 `covered` 或 `assert_coverage(...)`，重新核对分母与阈值 |
| `mark_function(...)` | 功能点描述和实际命中来源；旧函数标签不转换为已命中计数 |
| `as_dict()` 与旧报告 | 当前 schema 的 `report()` 和 `CoverageDatabase`，旧 JSON 不能直接当作 新版 数据库合并 |

例如将 operand 的边界值覆盖改为事务采样：

```python
from toffee import Bin, CoverGroupDef, CoverPointDef

coverage = CoverGroupDef("input", (
    CoverPointDef("operand", {
        "zero": Bin.values(0),
        "maximum": Bin.values(255),
        "middle": Bin.range(1, 254),
    }),
)).instantiate("accumulator.input")
coverage.sample({"operand": 0})
coverage.sample({"operand": 255})
coverage.sample({"operand": 3})
coverage.assert_coverage(100.0)
```

此例手动采样给定值。真实事务覆盖应在约定的提交、接受或观察位置采样，并保持
这一含义一致；需要记录实际接受就接在接受事件之后。
周期信号覆盖可以 bind 到时钟，再注册到 `Execution(coverage=[group])`。

旧版的 CovGroup 默认在全组覆盖达标后停止继续采样；新版 持续累计计数，用 `at_least`
表示达标次数。
因此旧计数与新计数不保证相等。跨运行合并时需要相同 schema 和采样规则，运行来源
由 run id 和 metadata 保存。cross、illegal、ignore、转移和原生策略见
[覆盖率指南](coverage.md)。

[`toffee-test`](https://github.com/XS-MLVP/toffee-test) 的 DUT 创建、周期采样和
`--toffee-report` 属于独立插件功能，新版 不提供这些同名入口。
测试执行用标准 pytest；DUT、波形、RTL 覆盖率、seed 和清理由项目 fixture 管理。
新版的统一报告可以组合 functional coverage 与 LCOV；它不等同于旧插件的完整 pytest
执行报告，pytest 的通过或失败仍是测试 verdict。完整配方见
[运行产物](verification-flow.md)。

## 当前迁移边界

- 一个原生 Backend 对应一个 XClock 调度域。多个独立 Execution 可以共享宿主 loop，
  但不提供跨时钟域共同时间轴；旧多时钟用例须单独核对。
- 原生 Expr 当前支持任意位宽无符号条件，不提供完整 signed 或算术表达式 IR。
- 普通 Sequence 和 FSM 等待采用非重叠匹配。coverage 的有界重叠模式不等于所有
  Trigger 都支持重叠，也不等于完整的时序断言系统。
- Backend、XClock 与 DUT 信号由 Execution 所在线程访问，同一 Backend 不能同时
  交给多个活跃 Execution。
- 模型 Port、Executor 优先级、Bundle 随机化及批量置值没有兼容入口。
  随机激励可以保留为使用局部 `random.Random(seed)` 的请求生成函数。
- 原生调度观察器当前适配 asyncio BaseEventLoop；其他 loop 实现需要独立适配和验证。
- SystemRDL 寄存器支持尚未实现，不能作为本次迁移可使用的 API。

完整使用限制见[语义与限制](../reference/semantics-and-limitations.md)。

## 按验证环境逐步迁移

1. 固定现有环境的输入序列、seed、DUT 配置和预期结果，并记录关键接受与响应周期。
2. 更新 Python 与原生绑定，在单个 Execution 内跑通信号读取、写入和周期等待。
3. 迁移 Bundle 绑定，核对字段、位宽、结构、部分更新和未知值的处理。
4. 将驱动和监视行为拆成 Driver 与 Monitor，明确接受条件和响应采样快照。
5. 保留参考模型的功能计算，改写接线与更新时机，再选择 FIFO、key 或固定延迟关联。
6. 替换测试入口与 fixture，增加有预算的 finish，明确组件、DUT 和外部任务的清理归属。
7. 重建覆盖定义和采样规则，最后核对报告与跨运行合并。

迁移验收应比较相同输入下的接受次数、响应次数、关联 key、结果和协议允许的延迟，
不能只比较最终值或通过数量。背压、连续 valid、乱序、漏响应、额外响应、取消和 reset
等原有场景也应继续触发各自的预期行为。

可先运行现有教程和乱序模型示例，确认新接口的使用方式：

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py
python3 -m examples.transactions.reference_model
```

具备原生绑定 后，再运行原生回归：

```bash
python3 -m pytest -q --require-xspcomm
python3 -m examples.transactions.reference_model --backend native
```

这些框架和教学示例检查不能代替项目 DUT 的 旧版 与 新版 对照回归。
