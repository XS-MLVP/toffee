# 配置 Execution 与 Backend

本页说明如何把已有 DUT 放入测试作用域，并在结束时释放资源。
以下片段假设项目已经提供 `dut`、注册的 `dut.clock` 和兼容的 xspcomm 绑定；
首次接入见[原生后端与项目 DUT](../getting-started/native-and-next.md)。

## Backend 的创建与关闭

在已有 async 测试函数中创建 Backend，用 `Execution` 管理本次运行，
并在 `finally` 中关闭 Backend：

```python
from toffee import Execution, RisingEdge, XCommClockBackend

backend = XCommClockBackend(dut.GetXClock())
try:
    async with Execution(backend, default_sample=RisingEdge(dut.clock)):
        await RisingEdge(dut.clock)
        # 在稳定上升沿读取并检查 DUT 输出。
finally:
    backend.close()
```

把 DUT 的释放放在 Backend 关闭之后。Execution 内使用 Trigger 等待，
不要调用 `dut.Step()` 或启动另一套时钟任务；暂停期间也一样。
退出 Execution 会清理本次等待与组件，Backend 和 DUT 的状态仍然保留。

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

有 Agent 的项目先构造并连接实例，再通过 `Execution(backend, agents=[agent])` 注册。
Execution 统一启动并反向清理组件；Agent 不需要独立 context，退出后应新建实例。
完整接线见[Agent 与参考模型](agents-and-reference-models.md)。

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

先使用默认预算运行测试。遇到 `SimulationNotSettledError` 时，检查报错的 tick、phase
和未完成回调，排除无限 `sleep(0)` 循环；独立后台服务按
[asyncio 集成指南](asyncio-and-pytest.md)使用 `external_task`。
仅对确定会结束的长任务链增加 `max_settle_rounds`。

完整默认值、控制权和关闭契约见[Execution 参考](../reference/execution.md)。
需要理解阶段收敛与三种预算的区别时，阅读[仿真调度与资源归属](../explanation/execution-model.md)。
