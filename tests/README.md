# 运行和维护测试

在仓库根目录执行：

```bash
python3 -m pytest -q --require-xspcomm
mkdocs build --strict
```

`--require-xspcomm` 严格检查原生绑定及匹配接口。
普通 `pytest` 可运行纯 Python 测试；完整回归使用上面的原生检查选项。
多页 HTML 报告的测试还需要系统安装 `genhtml`。

## 测试放在哪里

| 目录 | 主要检查什么 |
| --- | --- |
| `runtime/` | 时钟等待、订阅、取消、事务、Scoreboard 和退出清理 |
| `interfaces/` | 信号与 Bundle、Driver、Monitor、Agent 的行为 |
| `integration/` | 原生后端、asyncio 交接、教程和完整使用流程 |
| `coverage/` | bin、交叉覆盖、时序匹配、合并和报告 |
| `runner/` | 多进程运行、自动收集、原生合并和失败时的文件保留 |
| `release/` | 版本选择、打包和发布产物校验 |
| `typing/` | 公共 API 的类型正例和错误用法 |

入门示例和流水线覆盖示例也在默认测试范围内。
[功能索引](../docs/zh/reference/feature-map.md)列出指南、示例和对应回归。

## 新增测试

优先复用 `interfaces/conftest.py` 中的双后端时钟 fixture，
以及 `integration/conftest.py` 中的 DUT 替身。

测试应检查用户能观察到的结果：数据、接收时刻、异常、资源释放或报告内容。
测试围绕公开行为组织，尽量复用场景和 fixture。

修复问题时，先添加能复现问题的测试，再修改实现。
改动采样和生命周期后，同时检查正常退出、取消、异常退出及第二次运行。

教程中的代码片段有自动检查。修改教程或示例时，要保持两者一致，
并确认故意失败的练习仍报出文档说明的异常。

## 查看 Python 覆盖率

```bash
python3 -m pytest -q --require-xspcomm \
  --cov=toffee --cov-branch \
  --cov-report=json:output/python-coverage/coverage.json \
  --cov-report=html:output/python-coverage/html
```

这测量框架的 Python 代码。行覆盖率和分支覆盖率分别查看。
补用例时先定位遗漏的公开行为，再通过对应接口复现。

真实 RTL 的构建和测试见 `examples/integration/` 中的项目说明。
真实 RTL 验收显式运行各项目对应的构建与测试。

数组的纯 Python 契约在 `interfaces/test_signal_arrays.py`，真实 xcomm 叶子、原生触发、
覆盖率及 Driver/Monitor 在 `integration/test_unpacked_arrays.py`。运行数组集成测试时增加
`--require-xarrays`；它严格检查带 `XArray` 工厂的 xcomm（原生 ABI 6）。真实 RTL 的显式运行方式见
[数组示例](../examples/integration/arrays/README.md)。

递归 struct/array 的纯 Python 契约在 `interfaces/test_aggregate_views.py`，真实 xcomm 的
身份、四态、原生触发/coverage 及 Driver/Monitor 在 `integration/test_native_aggregate_views.py`。
增加 `--require-xviews` 可要求依赖清单中的原生 ABI 6、`XView` / `XStruct` / `XArray`
以及 Data 类型关联与显式视图 API。真实 RTL 的 DPI、mem_direct、VPI 三条路径见
[聚合示例](../examples/integration/aggregates/README.md)。
