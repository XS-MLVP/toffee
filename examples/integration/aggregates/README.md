# 递归 struct / array 的真实 RTL 验证

本例使用 `AggregateSignals.sv` 验证 packed struct、多维 packed 数组，以及包含 struct 数组和
65 位数组字段的 unpacked struct。Toffee 复用生成的实时叶子、存储、时钟回调和 XPort 注册。

需要匹配的 Picker 3 / xcomm 和 Python 绑定（**原生 ABI 6**），以及 Verilator 5.048+。
VPI 的 unpacked struct 场景要求 5.052+；本例三条路径在 5.052 上验证。使用仓库
`.github/native-dependencies.json` 固定的 xcomm，并配套使用同一 SDK 重新生成 DUT。

在 Toffee 仓库根目录执行：

```bash
export PICKER_BIN=/path/to/picker/build/bin/picker
export PICKER_TEMPLATE=/path/to/picker/template
export PYTHONPATH=/path/to/picker/build/dependence/xcomm/python:src
TOFFEE_AGGREGATE_RTL=1 python3 -m pytest -q \
  tests/integration/test_picker_aggregate_views.py --require-xviews
```

设置 `TOFFEE_AGGREGATE_RTL=1` 后显式构建 DPI、mem_direct、DPI + VPI 查询三条路径，
成功时显示 `3 passed`。

## 验证内容

- 从唯一配置库的 `dut.xcfg.Metadata("ports")` 读取 `Layout`，校验字段、真实边界、声明顺序和位宽。
- 原生 `XStruct` 接入 Bundle，`XArray` 接入 SignalArray，struct 数组的快照保留嵌套字段。
- packed 值默认按完整 Data 驱动与采样；字段访问使用 Data 的 `StructView()` / `ArrayView()`。
- 用真实 `data` / `data_0`、`req` / `req_valid` 端口验证前缀冲突不丢叶子；通过 metadata 绑定后的 packed Data 仍支持整值驱动。
- Driver / ReadyValidMonitor 传递不可变快照，Scoreboard 检查实际 RTL 输出。
- 保留默认端口边沿提交；VPI 查询输入由场景显式选择立即写。
- 先执行模型初始化，再驱动 VPI 输入。
- 静态嵌套字段条件和 65 位叶子的 coverage 均通过原生 xcomm 引擎运行。
- 多次传输后，旧快照保留原值，XPort 条目数与初始值一致。

纯 Python 契约和真实 xcomm 四态/所有权边界由对应单元、集成测试覆盖。
