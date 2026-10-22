# Driver：把请求变成输入时序

测试通常只需要表达“发送一次操作”。Driver 负责把这个操作转换成设计要求的信号和时序，
并告诉调用者输入在什么时候真正被接受。用例不必重复操作引脚或等待边沿。

## 先选提交方式，再看输入占几拍

有两个独立问题：

- **是否等输入接受后才返回？** Sync Driver 在 `await send()` 中完成驱动；
  Async Driver 在提交时立即返回 `XTransfer`，可以先提交多笔，再等待它们。
- **输入本身占几拍？** 单周期模板占一拍；多周期模板把一次输入分成多个阶段。
  这说的是输入时序，不是 DUT 计算结果需要多久。

| 提交方式 | 单周期输入 | 固定阶段的多周期输入 |
| --- | --- | --- |
| 等待输入接受 | `SyncSingleCycleDriver` | `SyncMultiCycleDriver` |
| 立即提交，稍后等待 | `AsyncSingleCycleDriver` | `AsyncMultiCycleDriver` |

这里的“异步”不取决于函数有没有写成 `async def`。
“同步”也不表示阻塞整个 Python 事件循环，只是当前调用要等输入接受。

输入时序不是固定阶段时，继承 `SyncDriver` 或 `AsyncDriver`，
在 `_drive_one()` 中定义实际的握手、阶段和接受条件。

## send 和 submit 分别等什么

| 调用 | SyncDriver | AsyncDriver |
| --- | --- | --- |
| `driver.send(request)` | 返回尚未运行的协程 | 立即提交，返回输入句柄 |
| `await driver.send(request)` | 驱动并等到输入接受 | 提交并等到输入接受 |
| `driver.submit(request)` | 不提供 | 立即提交，返回等待响应的事务句柄 |

`send()` 完成只表示输入已接受，不表示输出已经回来。
`submit()` 用于还要跟踪输出的事务，由响应检查方完成句柄；通常通过 Agent 使用。

`XTransfer` 在完整输入被接受后进入 `PROCESSING`。
`cancel()` 只能撤销尚未接受的请求；关闭 Driver 时仍未完成的已接受事务会报告
`DriverIncompleteError`，不会当作正常完成。

## 多周期输入能不能重叠

`max_active=1` 让一笔输入完成后再开始下一笔。
更大的值允许不同输入占用不同阶段，但不允许两笔同时写同一组信号。

多周期模板使用 `DriveStage(name, bits, idle, encode, cycles)` 描述各阶段：
阶段有自己的信号、空闲值、编码函数和时长。不同阶段的信号不能重叠。
输入在最后阶段的最后一拍才算接受。

若阶段会因反压延长，或多个阶段要使用同一端口，就在自定义 Driver 中使用
`async with self.resource_lock(name)` 管理占用。
同名资源只在该 Driver 内互斥，不会替硬件推断仲裁规则。
输入阶段重叠与响应仍未返回是两件事，应分别配置。

## 关闭时恢复输入

`SignalDriver` 管理驱动信号的独占使用、方向检查和空闲恢复。
活动中的异步 Driver 用 `await driver.aclose()` 关闭，或放进异步上下文：

```python
from toffee import AsyncSingleCycleDriver, Bundle, Execution

async with Execution(backend):
    async with AsyncSingleCycleDriver(
        dut.clock, Bundle(data=dut.data), idle={"data": 0},
    ) as driver:
        inputs = [driver.send({"data": value}) for value in (1, 2, 3)]
        accepted = [await item for item in inputs]
```

上例先提交三笔，再等待各自的接受事件，没有等待 DUT 响应。
项目用例通常通过 [Agent](agents-and-reference-models.md) 使用 Driver；
具体时序对照见[流水输入示例](pipeline-examples.md)。

## 实现自己的多周期输入

固定阶段输入可直接使用框架模板，测试只需要提交业务请求：

```python
from toffee import AsyncMultiCycleDriver, DriveStage

driver = AsyncMultiCycleDriver(
    clock,
    (
        DriveStage("address", address, 0, lambda request: request.address),
        DriveStage("payload", payload, 0, lambda request: request.payload, cycles=2),
    ),
    max_active=2,  # 两笔请求可以占用不同阶段；同一阶段始终互斥
)
```

`SyncMultiCycleDriver` 接受同一组阶段定义，但调用方用 `await send()` 等待输入接受。
`AsyncMultiCycleDriver` 的 `send()` 立即提交、接受即完成；`submit()` 立即提交，
接受后由 Monitor/Scoreboard 完成响应关联。启动时模板写入各阶段 idle，
取消或异常时恢复当前阶段 idle。`max_active` 限制同时进行的输入发射过程，
与已接受但尚未观察到响应的事务数无关。

当阶段持续时间不能预先写成固定周期，由具体 Driver 明确写出时序；以下 0 空闲值
只属于这个示例接口：

```python
from toffee import ClockCycles, SyncDriver

class ThreeCycleInput(SyncDriver[int]):
    def __init__(self, clock, data):
        self.clock, self.data = clock, data
        super().__init__((data,), name="three-cycle")

    async def _drive_one(self, value):
        self.data.Set(value)
        try:
            return await ClockCycles(self.clock, 3)
        finally:
            self.data.Set(0)
```

`_drive_one()` 返回输入实际接受的 XEvent，不能返回响应值或 None。
固定阶段需要后台提交时可直接使用 AsyncMultiCycleDriver；动态握手的后台提交
继承 AsyncDriver，并声明 capacity/max_active；无需另造 pipeline 基类。
并发占用同一组信号时，在具体时序中使用 resource_lock，完整实现见流水输入示例。

## 单周期模板的参数

两类 SingleCycleDriver 都接受 clock、标量/Bundle/packed/序列数据节点、idle，以及
可选 `encoder(request)`。encoder 必须同步返回符合驱动节点形状的值。
SyncSingleCycleDriver 使用显式 idle 值，在驱动结束或取消时恢复；AsyncSingleCycleDriver
还允许 `idle(last_request)`，启动时传 None，并在每次接受后恢复计算出的 idle。
callable idle 必须同时处理 None 和已接受请求。关闭只清理工作并释放 ownership，
不自动覆盖已经计算出的 idle。需要回到初始 idle 时，在所有输入接受后、退出前显式
调用 `driver.quiesce()`，此时 callable idle 再次接收 None。

```python
driver = AsyncSingleCycleDriver(
    clock, data, idle=lambda previous: 0 if previous is None else previous,
    encoder=lambda request: request, capacity=8,
)
```

这个例子在接受后保持上一笔输入；只有项目协议允许这种 idle 策略时才使用。
若要核对输出，不要直接 driver.submit 后无人接管响应：通过
[Agent](agents-and-reference-models.md) 或 [Scoreboard](scoreboard.md) 完成响应关联。
