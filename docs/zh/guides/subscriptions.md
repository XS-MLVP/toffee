# 持久订阅与同步快照

## `@on` 持久订阅

```python
import asyncio

from toffee import Execution, RisingEdge, on

snapshots = asyncio.Queue()

# clock 与 payload 是项目已经绑定的时钟和 Bundle。
@on(RisingEdge(clock), capture=lambda event: (event.tick, payload.sample()))
async def observe(snapshot):
    snapshots.put_nowait(snapshot)

async with Execution(backend) as execution:
    execution.subscribe(observe.bind())
    tick, value = await snapshots.get()
    # 消费命中 phase 保存的不可变 BundleValue。
    assert value == expected
```

`await trigger` 是 one-shot registration；`@on` 使用同一 Trigger 编译路径，但命中后
自动 rearm，并把结果送入 handler queue。

- `lossless`：队列满时显式失败；
- `latest`：队列满时丢弃旧项目，只保留新值；
- `capture=` 必须是同步函数，用于在命中 phase 立即制作不可变 snapshot；
- handler 必须是 `async def`。

有 capture 时，handler 接收 capture 的返回值；省略 capture 时接收 XEvent。
handler 可能延迟运行，不能靠重新读取 live signal 得到原采样时刻的数据。
上例的 trigger 已绑定，因此 `.bind()` 不需要参数；`@on(handshake)` 这类以 trigger
工厂声明的订阅才需要 `.bind(dut)`。

Execution 退出时会取消并回收所有 subscription。

可运行的纯 Python 版本：

```bash
python3 examples/triggers/subscription.py
```

## 与 Monitor 的选择

日常用例需要 await recv 或响应检查时使用 [Monitor](monitors.md)。`@on` 适合组件作者
实现持续处理的 handler，例如缓存采样记录或接收状态事件；handler 不能阻塞 event loop。
连续通知的 capture 发生在命中 phase，handler 处理较慢也不会改变已经保存的值。
lossless 溢出抛 SubscriptionOverflowError，handler/capture 异常使 Execution 失败，退出仍回收任务。

装饰器返回 XSubscriptionSpec，bind 返回 BoundSubscriptionSpec；Execution.subscribe
返回 Subscription 运行句柄。需要提前取消时可在组件内部调用
`execution.reactor.cancel_subscription(subscription)`；普通用例由 Execution 统一清理。
值条件的 enter/each_sample/change 模式沿用 Trigger 定义；持续订阅不会自动把 enter
变成逐周期电平通知。
