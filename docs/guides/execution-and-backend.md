# Execution 与 Backend

`Execution` 是一个测试的运行作用域，负责注册 Trigger、推进仿真和分发事件。

环境可以预先创建并连接接口实例，再通过 `Execution(backend, agents=[agent])` 注册。
Execution 统一启动 Agent，退出时先检查和清理组件，再关闭运行资源。Agent 不需要
独立的 context；普通用例直接调用其实例方法。具体示例见
[Agent 与 reference model](agents-and-reference-models.md)。

```python
async with Execution(
    backend,
    default_sample=RisingEdge(dut.clock),
):
    await RisingEdge(dut.clock)
```

退出 `Execution` 时，当前测试注册的 watcher、subscription 和编译缓存会被清理。
Backend 与 DUT 状态继续保留。

对于 `XCommClockBackend`，创建 Backend 时不接管时钟，进入 Execution 前仍可用
`dut.Step(...)` 完成初始化。进入 Execution 时，原生 XEngine 独占对应的 XClock，
直到退出时完成注册清理并释放。作用域内（包括暂停期间）调用 `dut.Step`、
`clock.Step`、`RawStep`、`StepHalf` 或 `Reset` 会立即抛出错误；旧的
`AStep`、`ANext`、`ACondition`、`RunStep` 也不可混用，请用 toffee Trigger 等待。
不同 Backend 包装同一个 XClock，也不能同时接管它。

正常退出、测试体异常或启动失败后，时钟恢复手动控制。释放不会推进或复位时钟；
若退出时停在半周期中间，手动调用完整周期 `Step` 前需用 `StepHalf` 完成剩余半周期。
直接使用 Backend 的 `arm` / `run_until` 时，会在首次调用时接管时钟并持有到
`close()`；进入 Execution 前须先移除直接注册的 watcher。

## Backend 的创建与关闭

```python
backend = XCommClockBackend(dut.GetXClock())
try:
    async with Execution(backend):
        ...
finally:
    backend.close()
```

`Execution` 不关闭 Backend。创建 Backend 的代码负责调用 `close()`。

## 在 pytest 中复用 Backend

```python
import pytest

from toffee import Execution, XCommClockBackend


@pytest.fixture(scope="session")
def backend(dut):
    backend = XCommClockBackend(dut.GetXClock())
    try:
        yield backend
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_a(backend):
    async with Execution(backend):
        ...


@pytest.mark.asyncio
async def test_b(backend):
    async with Execution(backend):
        ...
```

该配方要求项目的 dut fixture 同样使用 session scope；函数级 dut 应搭配函数级
backend fixture，完整配方见[模型与测试环境教程](../getting-started/reference-model.md)。
在多个 case 之间复用 Backend 时，时钟和 DUT 状态不会自动复位。需要确定初态的测试
应在进入 `Execution` 后执行 reset sequence。

## 输入提交与显式暂停

Execution 会等待当前阶段已就绪的任务及其级联唤醒处理完，再推进仿真。使用
AsyncDriver 时，后台输入由 Driver 管理，提交与资源交接无需插入 sleep(0)：

```python
async with Execution(backend), input_driver as driver:
    inputs = [driver.send(request) for request in requests]
    accepted = [await item for item in inputs]
```

这里 input_driver 是 AsyncDriver 子类实例；不同 Driver 的能力与完成条件由各自
契约定义，详见[流水输入示例](pipeline-examples.md)。
如果启动还依赖外部 I/O，应显式等待配置或初始化完成；就绪工作处理完不代表
外部操作已经完成。需要冻结时钟等待外部配置时使用 `Execution.paused()`。
暂停期间不要等待必须由仿真推进才能满足的 Trigger；这样的等待必须放到 context 外。

## 调度参数

```python
Execution(
    backend,
    default_sample=RisingEdge(dut.clock),
    max_batch_ticks=4096,
    quantum_ms=10.0,
    budget_check_interval=64,
    max_settle_rounds=100_000,
)
```

| 参数 | 含义 |
| --- | --- |
| `default_sample` | `Value` 和编译条件的默认采样 Trigger |
| `max_batch_ticks` | 单次 Backend 调用最多推进的 half ticks |
| `quantum_ms` | 单次连续推进的 wall-clock 时间上限 |
| `budget_check_interval` | 原生执行器检查时间预算的间隔 |
| `max_settle_rounds` | 单次阶段收敛检查允许交回 asyncio 的最大轮数；必须为正整数 |

同一 Backend 同一时间只能属于一个 active `Execution`。

域内任务若无限循环 `await asyncio.sleep(0)`，会让当前阶段始终有就绪工作。超过
预算会抛出 `SimulationNotSettledError`，包含 tick、phase 和未完成回调。增加预算
只能用于有限但较长的任务链；后台服务应通过 external_task 放到域外运行。
