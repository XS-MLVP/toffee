# 行为与限制

本页列出当前实现的行为契约。配置参数见[Execution 参考](execution.md)，
这些边界背后的原因见[仿真调度与资源归属](../explanation/execution-model.md)。

## 采样

- `RisingEdge` 和 `FallingEdge` 在稳定的 half-step 返回；
- `Value`、`ValueChange` 和编译条件只在指定的 sample phase 求值；
- 条件从注册后的下一个 sample phase 开始求值；
- 同一 phase 命中的事件由 Backend 批量返回。

## 生命周期

- `await trigger` 创建 one-shot registration；
- 取消等待会注销对应 watcher；
- subscription 在关闭或退出 `Execution` 时注销；
- `Execution` 清理当前测试的 trigger 和编译状态；
- Backend、DUT 状态和仿真时间由调用方管理。

## Driver 提交

- Driver 基类只要求 send 返回可等待对象，具体完成条件以具体 Driver 为准；
- AsyncDriver 的 send/submit 在调用时就提交，不等待调用方 await 才执行；
- send 返回输入接受句柄，接受时自动完成并释放容量；submit 返回待响应句柄；
- 取消等待者不撤销已提交工作，只有尚未接受的请求可以显式取消；
- 正常退出 AsyncDriver 会取消未接受输入，不隐式排空；应在退出前等待所需句柄；
- SyncDriver 仍返回执行驱动的 coroutine，不提供后台队列；具体协议与单周期时序保持各自契约。

## Agent 与事务检查

- Agent 是环境构造的实例，通过 Execution(agents=[...]) 注册，退出后新建实例；
- connect(ref) 不推进模型，实际接受后才调用同步 ref.accept；expected 覆盖不跳过模型更新；
- checked Agent.send 仅等待接受，响应仍由检查器跟踪；Driver.send 的输入独立完成契约不变；
- drain 等待调用前的提交水位；finish 封口、排空并观察指定窗口，不关闭 Monitor；
- 响应与接受事件使用显式单时钟 rising-stable phase；截止周期内正确响应优先于超时；
- FIFO/key 的额外或重复响应在严格结束检查失败，sampled 固定延迟模式允许忽略目标窗口外样本；
- 无人等待的后台失败仍从 finish/退出传播，已观察的同一个错误在清理时不重复报告；
- 共享模型的跨接口顺序、被动观察关联、多响应及 reset/flush 由项目明确，不自动推断。

## asyncio 阶段收敛

- asyncio 是唯一的 Python 调度器；Execution 观察 call_soon 即时回调，不另建执行队列；
- 当前 Execution 的即时回调及其级联唤醒处理完，才进入下一次时钟推进或 DriveStable 采样；
- 原生 Task、Lock、Queue、Event、Semaphore、gather、shield 和 TaskGroup 保持原有语义；
- 等待尚未完成的 Future 不阻止时钟，域内 create_task 的初次运行属于就绪工作；
- Timer、I/O、call_soon_threadsafe 是外部事件入口，实际交付后产生的域内工作才参与收敛；
  call_later(0) 不等同于阶段内的 sleep(0)，不承诺外部完成落在确定的仿真 tick；
- external_task 创建的任务在域外运行，清除隐式 Reactor 绑定，生命周期仍由调用方管理；
- 超过 max_settle_rounds 抛出 SimulationNotSettledError，不跳过工作强行推进；
- 当前适配 asyncio.BaseEventLoop，已在 CPython 3.12 默认 loop 验证；其他实现需单独适配；
- 不支持在活跃 Execution 期间替换 loop.call_soon；检测到后报错，退出时不覆盖宿主的新入口；
- 多个 Execution 共用观察入口，但分别判断收敛；不提供多时钟共同时间轴。

域外任务仍经过共享入口的检查，逻辑隔离不表示没有调度开销。详见
[asyncio 与 pytest](../guides/asyncio-and-pytest.md)。

## 数据

- 原生条件支持任意位宽 XData，位向量可显式使用有符号或无符号解释；
- `bits`、`as_signed()`、`as_unsigned()` 和 `resize()` 指定类型；两个带类型的操作数须同位宽、同符号类型；
- 常量必须能由目标类型表示，算术结果按位宽截断；
- `ValueChange` 比较四态值的 aval/bval；
- 数据运算按两态位向量定义，不提供完整的 SystemVerilog 四态传播。

具体操作、溢出和除法规则见[表达式与资源](../guides/expressions-and-resources.md)。

## 当前限制

- 一个 `XCommClockBackend` 对应一个 clock domain；
- 普通 Sequence 等待采用非重叠匹配；覆盖采集可显式启用 `overlap=True`，并须设置正整数 `max_active`，见[时序覆盖](../guides/coverage-patterns.md)；
- `@pytrigger` 在每个 sample phase 执行 Python 条件函数；
- Backend 不能同时用于多个 active `Execution`；
- Backend、XClock 和 DUT signal 不支持跨线程并发访问；
- `Bundle` 和具体通道视图需要显式绑定；不存在通用 `Interface` 基类；
- coverage JSON 使用 Toffee schema，不是 UCIS。
