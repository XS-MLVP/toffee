# 定长数组的真实 RTL 验证

本例用同一份 RTL 验证 Picker 生成的定长 unpacked 数组端口，以及 xcomm `XArray`
的内部原生数组视图。DPI 和 mem_direct 两条路径都由 xcomm 的内部共享数组后端
提供，并运行真实 RTL。

环境需要 xcomm 的 `XArray` 工厂（原生 ABI 6）、匹配的 Python 绑定和 Picker 3 模板；
升级工具链后重新生成 DUT。

在 Toffee 仓库根目录执行，替换工具和绑定路径：

```bash
export PICKER_BIN=/path/to/picker/build/bin/picker
export PICKER_TEMPLATE=/path/to/picker/template
export PYTHONPATH=/path/to/picker/build/dependence/xcomm/python:src
TOFFEE_ARRAY_RTL=1 python3 -m pytest -q \
  tests/integration/test_picker_unpacked_arrays.py --require-xarrays
```

设置 `TOFFEE_ARRAY_RTL=1` 后测试会构建两种访问方式，成功时显示 `2 passed`。
上面的显式 `PYTHONPATH` 可用于选择源码构建；`--require-xarrays` 会严格检查
`XArray` 工厂和匹配的 ABI，诊断环境选择错误。

DPI 和 mem_direct 已在 Verilator **5.026、5.048、5.052** 上验证。
使用其他版本时，将该版本的 `bin` 放到 PATH 前面，并设置对应的 `VERILATOR_ROOT`，再运行
同一条命令。

## 验证内容

- 根据唯一配置库 `dut.xcfg.Metadata("ports")` 的 `Unpacked` 元数据绑定二维和三维端口。
- 端口数组适配为结构只读的 `SignalArray`，保留来源叶子，采样得到不可变 `ArrayValue`。
- 绑定后保留 RTL 边界；快照按真实下标访问，驱动接受真实下标 mapping 或声明顺序序列。
- 65 位元素、负下标和各维升降序；结果数组与输入使用不同方向。
- 保持默认 Rise 写模式，由生成的 XPort/XClock 提交。
- Driver/ReadyValidMonitor 通过同一批 XData 叶子传递不可变数组快照。
- 数组常量下标触发条件在原生引擎运行，Scoreboard 检查电路实际输出。
- mem_direct 内部数组使用零起始存储坐标及 sizeof 推导的步长；扁平列表也可读取同一底层存储。

可手工导出后运行同一验证脚本：

```bash
"$PICKER_BIN" export examples/integration/arrays/UnpackedSignals.sv \
  --sname UnpackedSignals --lang python --sim verilator --rw mem_direct \
  --sdir "$PICKER_TEMPLATE" --tdir output/arrays/UnpackedSignals \
  --autobuild false -V '-Wno-fatal'
make -C output/arrays/UnpackedSignals NPROC=4
PYTHONPATH="output/arrays:$PYTHONPATH" python3 examples/integration/arrays/verify.py \
  --access mem_direct
```

中文用法与契约见[数据指南](../../../docs/zh/guides/data.md#unpacked-arrays)，英文见
[data guide](../../../docs/en/guides/data.md#fixed-unpacked-arrays)。
