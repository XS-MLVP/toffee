# 性能测试

这里的测试用于比较框架改动前后的开销，不直接代表真实 RTL 的仿真速度。
先安装匹配的 xspcomm，在仓库根目录运行。

## 调度与等待

```bash
python3 benchmarks/benchmark_runtime.py --ticks 200000 --repeats 5 --json
```

测试比较 XClock 步进、半周期推进、原生条件与状态机等待、Python 条件轮询，
以及每周期唤醒协程的开销。时钟不执行真实 RTL 计算。

## 覆盖率

```bash
python3 benchmarks/benchmark_coverage.py --iterations 100000 --repeats 5 --json
python3 benchmarks/benchmark_coverage.py --native --iterations 20000 --repeats 5
```

前一项比较事务字段读取和采样明细的开销；后一项比较原生采样、重叠匹配和诊断计数。
需要比较真实 RTL 的覆盖率开销时，使用
`examples/integration/e203/benchmark_coverage.py`。

## Driver 和 asyncio 调度

```bash
python3 benchmarks/benchmark_driver_handoffs.py --requests 256 --repeats 7
python3 benchmarks/benchmark_asyncio_observer.py --repeats 7
```

前者比较输入阶段的资源交接，后者分别测量仿真协程与外部任务的调度开销。
同时检查接收时刻和最终仿真时间，不能把少执行了一段时序当成提速。

## 怎样比较结果

保持机器、编译选项、原生依赖、波形与覆盖率开关、迭代次数一致。
重复运行，比较中位数，并保留 JSON 中的每次测量。
不要把空时钟的结果直接换算成某个 DUT 的加速倍数。
