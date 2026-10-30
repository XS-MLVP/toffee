# 可选协议视图

通用 Driver、Monitor 与 Agent 不依赖 ready/valid。以下工具仅适用于明确采用该
握手协议的项目；其它协议在自己的组件中实现接受规则。

## ReadyValid

```python
from toffee import Bundle, ReadyValid, Role

producer = ReadyValid(
    clock=dut.clock,
    valid=dut.valid,
    ready=dut.ready,
    bits=Bundle(data=dut.data),
    role=Role.PRODUCER,
    name="request",
)
```

角色是 testbench 的视角：

- `PRODUCER` 驱动 valid/bits，观察 ready；
- `CONSUMER` 驱动 ready，观察 valid/bits；
- `MONITOR` 只观察。

`ReadyValid.fire` 是在 `DriveStable` 采样的 persistent-capable compiled trigger。

## 协议采样和驱动函数

`ReadyValidMonitor(producer, decoder=...)` 在其 fire 候选事件保存不可变 payload，
在对应上升沿确认后交付 `Transfer`。decoder 接收快照，不能依赖已变化的 live 信号。
这与 SamplingMonitor 在传入 Trigger 命中时直接记录的契约不同。
生命周期、lossless/latest 和错误传播规则见 [Monitor](monitors.md)。

项目可以将 `drive_ready_valid` 用在自己的 SyncDriver 或 AsyncDriver 子类中：

```python
async def _drive_one(self, request):
    return await drive_ready_valid(
        self.port.clock, self.port.valid, self.port.ready,
        lambda: self.port.bits.drive(request),
        bits=self.port.bits,
    )
```

子类仍须声明所驱动信号。这个辅助函数在下降稳定阶段驱动 payload/valid，
在所有同阶段驱动完成后的 `DriveStable` 等待 ready，
在接受上升沿后撤销 valid；drive 回调必须同步且返回 None。`bits=` 让 helper
在采样前提交 picker Rise-mode payload，尤其适用于 ready 依赖当前 payload 的端口；
Rise-mode valid 也由 helper 提交和撤销，testcase 不接触 picker 写入细节。
具体是否适用由项目协议决定。
框架没有 ReadyValidDriver，也不会向通用 Driver 注入该时序。

可选的 `admit=lambda: bool(...)` 在实际驱动时隙、编码之前判断是否允许启动。
返回 False 时不编码、不写 payload/valid，下个驱动时隙重试；返回 True 后与 drive
连续执行，中间没有 await。回调必须同步并返回 bool，异常正常传给发送任务。
这是启动条件，不是 ready：启动后不再判断 admit，仍按协议保持 payload 等待接受。
等待预算与取消由调用方负责；测试用例无需接触底层边沿。

`Decoupled` 是 ReadyValid 的别名；`flipped()` 创建相反角色视图，不复制信号或改变 DUT
连接。接口角色和信号方向不一致会在绑定/取得驱动所有权时失败。
