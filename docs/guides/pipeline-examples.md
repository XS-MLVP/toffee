# 流水输入与阶段重叠：可运行示例

本页区分三件事：连续提交输入、多个输入驱动过程重叠，以及等待多个在途响应。
示例使用支持后台提交的 `AsyncDriver`：`send()` 只跟踪输入接受，
`submit()` 跟踪后续响应；二者都在调用时提交，随后可以等待返回的句柄。
这些是此模板的能力，不是所有 Driver 的统一要求；`SyncDriver` 仍通过 await 执行 send，
具体协议 Driver 则遵循各自的发送与完成约定。

所有输入时序都写在具体 `_drive_one()` 中，无需继承 SingleCycle 类。
示例不使用 ready/valid、reset/flush，也不推断 DUT 协议。

## 运行与选择

从仓库根目录执行：

```bash
python3 -m pip install -e '.[test]'
python3 examples/transactions/pipeline.py
```

默认运行六个成功场景，打印接受、响应和逐周期采样记录。
完整代码在 [pipeline.py](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/pipeline.py)。
它包含替身、Driver、Monitor、Scoreboard 连接和清理，可以直接运行。

| 想验证的行为 | 单独运行的参数 | 关键机制 |
| --- | --- | --- |
| 每拍发一个输入，先前响应仍在途中 | `--case continuous` | submit、max_active=1、固定延迟关联 |
| 保序响应，但延迟不固定 | `--case fifo` | 默认 FIFO 关联 |
| 完整输入串行经过两个阶段 | `--case serial` | max_active=1 |
| 不同请求分别占用两个阶段 | `--case overlap` | max_active=2、按阶段加锁 |
| 每笔接受后才提交下一笔 | `--case send` | 逐次 await send，即使 max_active=2 仍串行 |
| 响应倒序返回 | `--case out-of-order` | request_key / response_key |

例如：

```bash
python3 examples/transactions/pipeline.py --case overlap
```

有原生绑定 时，同一组代码也可以运行在真实 XClock 上：

```bash
python3 examples/transactions/pipeline.py --backend native
```

这验证原生调度，不需要 RTL。若 绑定 位于构建目录，先按
[安装指南](../getting-started/installation.md)配置 PYTHONPATH；pytest 的本地构建自动
发现不会应用到普通脚本。

## 替身的明确约定

ToyPipeline 在每个上升沿采样实际被驱动的信号，不用 Driver 的 Python 日志代替采样。
它是以下示例共用的小型 DUT 替身：

- 正整数 1、2、3 表示三个请求，0 是这个替身自定的 idle 值。
- 普通输入采到正整数即接受；输出为 `Response(tag=request, value=request * 10)`。
- 多阶段输入的第一阶段占 1 拍，第二阶段占 3 拍；第二阶段第三次采样才算完整输入接受。
- 打印的 tick 为 half-tick；本例从 tick 0 开始，上升沿位于 2、4、6……。

这些约定由替身和具体 Driver 明确提供。迁到项目时，应换成设计自己的输入接受点、
输出采样方式和空闲策略。

## 1. 连续输入，不等待前一笔响应

示例的 Input 直接继承 AsyncDriver，设置 `max_active=1`：

```python
class Input(AsyncDriver[int, Response]):
    def __init__(self, toy, *, capacity=8):
        self.clock, self.data = toy.clock, toy.first
        super().__init__((self.data,), name="input", capacity=capacity, max_active=1)

    async def _drive_one(self, request):
        self.data.Set(request)
        try:
            return await ClockCycles(self.clock, 1)
        finally:
            self.data.Set(0)
```

一次 `_drive_one()` 只负责输入接受，响应由 Monitor 和 Scoreboard 跟踪。
`run_responses()` 先提交全部输入，再等待结束：

```python
transfers = [board.submit(request) for request in (1, 2, 3)]
status = await board.finish(timeout_cycles=30, observe_cycles=1)
values = [await transfer for transfer in transfers]
```

不要在提交循环中逐笔 `await board.submit(request)`，否则下一笔输入会等前一笔响应。
这里最后读取句柄时，事务已经由 finish 排空。

固定延迟场景显式设置 `latency_cycles=3`，实际采样如下：

| 周期 | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | --- | --- | --- | --- | --- | --- |
| 输入请求 | 1 | 2 | 3 | — | — | — |
| 响应 tag | — | — | — | 1 | 2 | 3 |

输出为：

```text
accepted=[(1, 2), (2, 4), (3, 6)]
responses (tag, tick): [(1, 8), (2, 10), (3, 12)]
```

第三笔输入已经接受时，第一笔响应仍未返回。因此 `max_active=1` 并不意味着只能有
一个在途响应。通用串行 worker 复用发射协程；本例的连续输入没有额外空拍。

## 2. 两个输入阶段：串行与重叠

QueuedStages 继承 AsyncDriver，使用以下具体驱动函数。每个阶段独立加锁，避免
不同请求同时写同一组信号：

```python
async def drive_stages(driver, request):
    async with driver.resource_lock("first"):
        driver.first.Set(request)
        try:
            await ClockCycles(driver.clock, 1)
        finally:
            driver.first.Set(0)
    async with driver.resource_lock("second"):
        driver.second.Set(request)
        try:
            return await ClockCycles(driver.clock, driver.second_cycles)
        finally:
            driver.second.Set(0)
```

`resource_lock("first")` 只保护第一阶段，第二阶段有自己的锁。若用一把锁包住整个
函数，两笔完整输入仍会串行。

`resource_lock()` 按当前 Driver 内的资源名复用锁；具体 Driver 声明占用范围，
测试只提交输入。Execution 在下一次时钟推进和 DriveStable 采样前，等待本阶段
已就绪的回调及其级联唤醒处理完，阶段代码无需补 sleep(0)。
锁不决定占用几拍或何时转交数据；这些仍由具体驱动函数中的等待和信号操作定义。

比较：

```bash
python3 examples/transactions/pipeline.py --case serial
python3 examples/transactions/pipeline.py --case overlap
```

| max_active | 请求 1 接受周期 | 请求 2 接受周期 | 请求 3 接受周期 |
| --- | --- | --- | --- |
| 1 | 4 | 8 | 12 |
| 2 | 4 | 7 | 10 |

重叠模式的实际采样片段：

| 周期 | 第一阶段信号 | 第二阶段信号 | 完整输入接受 |
| --- | --- | --- | --- |
| 1 | 1 | 0 | — |
| 2 | 2 | 1 | — |
| 3 | 0 | 1 | — |
| 4 | 0 | 1 | 请求 1 |
| 5 | 3 | 2 | — |
| 6 | 0 | 2 | — |
| 7 | 0 | 2 | 请求 2 |

第 2 拍请求 2 在第一阶段、请求 1 在第二阶段，这是实际信号采样意义上的重叠。
第二阶段占 3 拍，限制了持续吞吐；增大 max_active 不会自动让每个阶段都能每拍接受。

这组场景只演示输入接受，因此使用 `send()`，不创建待响应的业务事务：

```python
async with Execution(toy.backend), QueuedStages(toy, max_active=2) as driver:
    inputs = [driver.send(request) for request in (1, 2, 3)]
    accepted = [await item for item in inputs]
```

输入先全部提交，由 Driver 按 max_active 和阶段资源执行；随后按顺序读取句柄不会
串行化已提交的输入。句柄在接受时进入 COMPLETED，结果、accepted_event 和
completed_event 是同一个接受事件，同时释放容量，不进入 recv_accepted 响应关联流。
多个批次可以共用同一个 Driver context。

即使没有等待者，输入也会执行；重复 await 同一个句柄只读取同一个结果，不会重新
发送。容量不足或 Driver 未启动会直接在 send 调用处报错。若只是准备稍后发送的
数据，应先保存 request，再在需要提交时调用 send。

未接受输入可以显式 `item.cancel()`；取消一个等待者不会撤销底层输入。驱动失败
从等待句柄或 Driver context 退出传播，已经观察的同一失败不重复报告。
context 退出会取消尚未接受的输入并回收 worker，不隐式等所有提交完成；正常用例
应在退出前等待所需句柄。使用 `submit()` 的事务则由响应检查方负责完成。

## 3. 逐笔等待与批量提交

```bash
python3 examples/transactions/pipeline.py --case send
```

同一个 QueuedStages，即使设置 max_active=2，下面的写法仍会逐笔发送：

```python
for request in (1, 2, 3):
    await driver.send(request)
```

下一笔在上一笔接受后才提交，因此接受 tick 是 8、16、24。改用上一节的先提交、
后等待，接受 tick 是 8、14、20。输入阶段的并发由 Driver 管理，用户无需创建任务。

这不改变 SyncDriver 的能力：它仍适用于调用方直接等待输入接受的用法，没有后台
提交接口。具体项目可以继续选择适合自身发送时序和生命周期的 Driver。

## 4. FIFO、固定延迟和 key 关联

下面是 `run_responses()` 中三种 bind 配置的区别，其余生命周期相同：

| 模式 | 传给 bind 的关联参数 | 替身实际响应延迟 |
| --- | --- | --- |
| continuous | `latency_cycles=3` | 每笔均为 3 周期 |
| fifo | 不传关联参数 | 三笔分别为 3、3、4 周期，响应保序 |
| out-of-order | request_key / response_key | 三笔分别为 5、3、1 周期 |

所有模式都显式传入 clock 和 response_timeout_cycles。固定延迟模式会检查指定周期，
并非仅对后来到达的响应做匹配。

乱序配置：

```python
board.bind(
    execution, driver=driver, monitor=monitor, clock=clock,
    response_timeout_cycles=6,
    request_key=lambda request: request,
    response_key=lambda response: response.tag,
)
```

三笔请求仍按 1、2、3 接受，响应顺序为 3、2、1：

```text
accepted=[(1, 2), (2, 4), (3, 6)]
responses (tag, tick): [(3, 8), (2, 10), (1, 12)]
```

每个 XTransfer 收到与自己的 tag 对应的响应。先提交全部事务后，再按提交顺序读取
句柄，不会改变响应完成顺序。在途请求的 key 必须唯一，不能用重复 tag 模糊关联。

## 5. 失败演示

以下命令**预期以非零退出并报告异常**，不包含在默认成功场景中：

```bash
python3 examples/transactions/pipeline.py --case missing
python3 examples/transactions/pipeline.py --case late
python3 examples/transactions/pipeline.py --case wrong-fifo
python3 examples/transactions/pipeline.py --case capacity
```

| 场景 | 人为设置 | 预期失败 |
| --- | --- | --- |
| missing | 请求接受后不产生响应，响应预算为 2 周期 | ScoreboardTimeoutError，operation=response |
| late | 固定延迟要求 3 周期，替身安排第 4 周期响应 | ScoreboardTimeoutError，operation=fixed_latency；错过目标就失败，无需等迟到响应 |
| wrong-fifo | 响应倒序返回，但选择 FIFO | ScoreboardMismatch，第一笔期望 tag 1，实际 tag 3 |
| capacity | Driver 容量为 2，一次提交 3 笔 | RuntimeError，submission capacity is full |

异常路径仍退出 Scoreboard、Driver、Execution 并关闭 backend。异步后台错误不能
靠不 await transfer 隐藏；这些场景都经过 finish 或 context 的失败传播。

## 参数不要混用

| 参数 | 限制什么 | 本例 |
| --- | --- | --- |
| `max_active` | 同时运行多少个输入 `_drive_one()` | 连续输入为 1；两阶段重叠为 2 |
| Driver `capacity` | 已提交但尚未结束的总数，包括排队、驱动中与等待响应 | 默认为 8；满时 submit 报错，不自动等待 |
| Scoreboard `capacity` | 活跃事务与未匹配观察的容量 | 本例使用默认值 64，与 Driver 容量分别检查 |
| `latency_cycles` | 固定的接受到响应窗口 | continuous 为 3 |
| `response_timeout_cycles` | 每笔输入接受后的响应预算 | 成功场景为 6，固定延迟以其更早的指定窗口为准 |
| `finish(timeout_cycles=...)` | 排空加尾部观察的总预算 | 本例为 30，包含 observe_cycles=1 |

capacity 不是流水级数，max_active 也不是最多在途响应数。
更完整的取消与结束语义见 [Scoreboard 指南](scoreboard.md)。

## 自动化验证

```bash
python3 -m pytest -q tests/integration/test_pipeline_examples.py --require-xspcomm
```

这些回归分别在 MemoryBackend 和原生 XClock 上验证：连续输入无空拍、三笔
响应同时在途、固定延迟截止周期成功、两种 Driver 在 max_active=1/2 下的真实采样、
阶段资源互斥、FIFO 变延迟、倒序 key 匹配，以及四种预期失败。
还检查任务、watcher、backend 租约、信号 idle 与原生回调的清理。

`tests/integration/test_driver_handoffs.py` 另外覆盖无手动 yield 的级联锁交接、
DriveStable 前的写入、并发名额交接、公平性、取消、异常，以及外部任务不阻塞仿真。

测试复用上述示例的 Driver 和替身，避免维护另一套仅供测试的近似实现。测试中的墙钟
timeout 只防止挂起，缺失或迟到响应必须由 Scoreboard 的仿真周期预算判失败。
