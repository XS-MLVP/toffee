# 定长数组的真实 RTL 验证

本例用同一份 RTL 检查 Picker 的 DPI 和 mem_direct 数组接口，不使用 Python 行为替身。
需要带 `XDataArray` 的 xcomm ABI 4、匹配的 Python 绑定和更新后的 Picker 模板；仅升级
Toffee 不会使旧 Picker 获得数组能力。库、绑定和 DUT 必须一起重建。

在 Toffee 仓库根目录执行，替换工具和绑定路径：

```bash
export PICKER_BIN=/path/to/picker/build/bin/picker
export PICKER_TEMPLATE=/path/to/picker/template
export PYTHONPATH=/path/to/picker/build/dependence/xcomm/python:src
TOFFEE_ARRAY_RTL=1 python3 -m pytest -q \
  tests/integration/test_picker_unpacked_arrays.py --require-xarrays
```

测试将构建两种访问方式，成功时显示 `2 passed`。不设置 `TOFFEE_ARRAY_RTL=1` 时，
默认框架回归不会隐式构建 RTL。若已安装的旧 xspcomm 遮蔽源码构建，使用上面的显式
`PYTHONPATH`；`--require-xarrays` 会在缺少 XDataArray 或 ABI 不匹配时失败，而非静默跳过。
发布用的原生依赖清单仍固定真实的已发布版本；验证未发布数组分支时显式选择匹配构建，
不能将清单改成不存在的提交。

用其他 Verilator 验证时，将该版本的 `bin` 放到 PATH 前面，并设置对应的
`VERILATOR_ROOT`，再运行同一条命令。DPI/mem_direct 的验证锚点为 5.026、5.048、5.052，
不代表所有中间或未来版本逐一认证。VPI 数组尚未接入，本例不测试 VPI，也不为 5.026
增加 VPI 兼容路径。

## 验证内容

- 根据真实 `signals.json` 的 `Unpacked` 元数据绑定二维和三维端口。
- 65 位元素、负下标和各维升降序；结果数组与输入使用不同方向。
- 保持默认 Rise 写模式，由生成的 XPort/XClock 提交，不在适配器中改写模式。
- Driver/ReadyValidMonitor 通过同一批 XData 叶子传递不可变数组快照。
- 数组常量下标触发条件在原生引擎运行，Scoreboard 检查电路实际输出。
- mem_direct 内部数组使用零起始存储坐标及 sizeof 推导的步长；旧扁平列表仍可读取。

可手工导出后运行同一验证脚本：

```bash
"$PICKER_BIN" export examples/integration/arrays/UnpackedSignals.sv \
  --sname UnpackedSignals --lang python --sim verilator --rw mem_direct \
  --sdir "$PICKER_TEMPLATE" --tdir output/arrays/UnpackedSignals \
  --autobuild false -V '-Wno-fatal'
make -C output/arrays/UnpackedSignals NPROC=4
PYTHONPATH="output/arrays:$PYTHONPATH" python3 examples/integration/arrays/verify.py \
  --metadata output/arrays/UnpackedSignals/signals.json --access mem_direct
```

英文用法与契约见 [data guide](../../../docs/en/guides/data.md#fixed-unpacked-arrays)。
