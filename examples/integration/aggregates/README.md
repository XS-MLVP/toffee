# 递归 struct / array 的真实 RTL 验证

本例使用 `AggregateSignals.sv` 验证 packed struct、多维 packed 数组，以及包含 struct 数组和
65 位数组字段的 unpacked struct。Toffee 使用生成的实时叶子，不复制模拟器存储、不创建逐元素
时钟回调，也不新增 XPort 注册。

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

成功时显示 `3 passed`，分别构建 DPI、mem_direct、DPI + VPI 查询。未设置
`TOFFEE_AGGREGATE_RTL=1` 时默认回归不会隐式构建 RTL。

## 验证内容

- 用唯一配置库的 `dut.xcfg.Metadata("ports")` 中的 `Layout` 绑定递归结构，保留字段、真实边界、声明顺序和位宽；不依赖单独的 `signals.json`。
- 原生 `XStruct` 接入 Bundle，`XArray` 接入 SignalArray，struct 数组的快照保留嵌套字段。
- packed 值默认按完整 Data 驱动与采样；字段访问使用 Data 的 `StructView()` / `ArrayView()`。
- Driver / ReadyValidMonitor 传递不可变快照，Scoreboard 检查实际 RTL 输出。
- 保留默认端口边沿提交；VPI 查询输入由场景显式选择立即写，不在适配器中修改写模式。
- VPI 写入前先执行模型初始化，避免 Verilator 首次求值覆盖提前写入的值。
- 静态嵌套字段条件和 65 位叶子的 coverage 均通过原生 xcomm 引擎运行。
- 多次传输后，旧快照保持不变，XPort 条目数不增加。

本例不验证 UVS、VCS、GSIM 的聚合运行时支持，也不为没有 RTL 布局的内部 packed struct
推断字段偏移。纯 Python 契约和真实 xcomm 四态/所有权边界由对应单元、集成测试覆盖。
