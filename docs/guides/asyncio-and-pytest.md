# asyncio 与 pytest

Toffee 使用当前正在运行的 asyncio event loop，可以和 HTTP client/server、Queue、
Timer 以及其他异步任务同时运行。

本页主要面向宿主集成和组件开发。日常验证使用 Driver 提交、Monitor/Scoreboard 的
组件生命周期及 Trigger 等待，见[流水输入示例](pipeline-examples.md)。无需自行
创建并发任务来开启流水，也没有通用 parallel 接口。

asyncio 仍是唯一运行 Python Task 和回调的调度器。Execution 观察属于本次仿真的
即时回调，在它们及其继续唤醒的工作处理完之前，不推进时钟或进行 DriveStable 采样。
外部库使用的原生同步原语与任务保持兼容；这不要求测试作者使用这些 API。

## pytest

```python
import pytest

from toffee import Execution, RisingEdge


@pytest.mark.asyncio
async def test_counter(backend, dut):
    async with Execution(backend):
        await RisingEdge(dut.clock)
        assert int(dut.count.U()) == 1
```

pytest-asyncio 已经创建 event loop，测试中直接使用 `await` 即可。
完整 fixture 和可直接运行的测试见[模型与测试环境教程](../getting-started/reference-model.md)。

## 任务归属

注册的 Agent 由 Execution 启停；AsyncDriver 管理提交任务，Scoreboard 管理响应收集，
Monitor 由其唯一生命周期所有者关闭。
外部库自己创建的 Task 或 TaskGroup 仍由原所有者负责结束；Execution 的调度观察
不会自动取得这些任务的生命周期所有权。

AsyncDriver.send 在调用时已经提交并返回 XTransfer，可直接 await；返回值不是
coroutine，不能再传给要求 coroutine 的 create_task/TaskGroup.create_task。
迁移已有调用时，保存 send 返回的句柄，在需要处 await 即可；不要再为输入创建任务。
这不改变 SyncDriver 或具体协议 Driver 返回 coroutine 的接口。

## 外部库与后台服务

在 Execution 中创建的任务默认继承本次仿真的调度归属。外部服务或参考模型可以用
`execution.external_task(coro, name=None)` 显式在域外运行：

```python
async with Execution(backend) as execution:
    task = execution.external_task(fetch_configuration(), name="configuration")
    configuration = await task
    await FallingEdge(clock)
    apply_configuration(configuration)
```

它返回原生 Task，保留用户 ContextVar，但清除调度归属和隐式 Reactor 绑定。外部
任务不能直接 await XTrigger 或隐式驱动 DUT，应交付结果让仿真任务使用；其正常
继承上下文创建的后台子任务也在域外。已有 Task 不能被这个入口追溯改变归属。

直接 await 返回的 Task 保持 asyncio 取消传播；要保护已有外部任务，可使用下面的
TaskComplete。后台任务仍由创建者清理，退出 Execution 不会替它结束。

需要获取配置期间冻结时钟时，显式使用暂停：

```python
async with execution.paused():
    configuration = await execution.external_task(fetch_configuration())
```

域外任务不计入仿真屏障，但与其他任务共享 loop 的调度入口，仍有观察入口的检查开销。
详细限制见[行为与限制](../reference/semantics-and-limitations.md)。

## 等待外部任务

普通 coroutine 可以直接等待：

```python
response = await http_client.get(url)
await RisingEdge(dut.clock)
```

需要同时等待 DUT 和外部事件时使用 `AnyOf`：

```python
event = await AnyOf(
    Value(dut.done, 1, sample=RisingEdge(dut.clock)),
    QueueTrigger(control_queue),
    WallTimeout(2.0),
)
if event.kind is XEventKind.TIMEOUT:
    raise TimeoutError("no DUT result or control message within 2 seconds")
```

仿真在存在 DUT Trigger 或 subscription 时推进。若没有仿真 watcher，仅等待 HTTP、
Queue 或普通 Task，仿真时间保持不变。已经启动持续 Monitor/Scoreboard 时仍有 watcher，
即使调用方在等 HTTP，时钟仍可推进；需要冻结时显式使用 paused。

## 等待已有 task，不撤销它

`TaskComplete` 把普通任务结果包装为 XEvent，并用 shield 保护原任务：

```python
from toffee import AnyOf, TaskComplete, WallTimeout, XEventKind

# reference_task 由外层创建并负责最终 await 或取消。
event = await AnyOf(TaskComplete(reference_task), WallTimeout(2.0))
if event.kind is XEventKind.TIMEOUT:
    raise TimeoutError("reference task did not complete within 2 seconds")
result = event.value
```

超时或取消上述等待，都不会取消 reference_task；外层仍须负责它的最终清理。
直接写 `AnyOf(reference_task, ...)` 则会在该 task 未获胜时取消原任务。
同样，取消等待 `XTransfer` 的协程不会撤销底层事务；仅未接受请求可以显式
`transfer.cancel()`，详见 [Scoreboard](scoreboard.md)。

## 后台任务

```python
import asyncio
import contextlib

task = asyncio.create_task(run_reference_server())
try:
    async with Execution(backend):
        ...
finally:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
```

`Execution` 管理自身推进、订阅和显式注册的 Agent；用户创建的独立后台任务仍由
创建者负责结束。

## 工作线程

阻塞函数可通过 `asyncio.to_thread()` 调用。Backend、XClock 和 DUT 信号应由运行
`Execution` 的线程访问；工作线程的结果可通过 asyncio Queue 或 Event 返回。
