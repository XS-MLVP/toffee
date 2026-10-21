# 运行和维护测试

在仓库根目录执行：

```bash
python3 -m pytest -q --require-xspcomm
mkdocs build --strict
```

`--require-xspcomm` 会在原生绑定缺失或接口不匹配时失败。
普通 `pytest` 允许没有原生环境的用户运行纯 Python 测试；不能据此宣布完整回归通过。
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
[功能索引](../docs/reference/feature-map.md)列出指南、示例和对应回归。

## 新增测试

优先复用 `interfaces/conftest.py` 中的双后端时钟 fixture，
以及 `integration/conftest.py` 中的 DUT 替身。

测试应检查用户能观察到的结果：数据、接收时刻、异常、资源释放或报告内容。
不要只断言某个私有函数被调用，也不要为了覆盖配置分支而复制整套场景。

修复问题时，先添加能复现问题的测试，再修改实现。
改动采样和生命周期后，检查取消、异常退出及第二次运行，不能只测一次正常结束。

教程中的代码片段有自动检查。修改教程或示例时，要保持两者一致，
并确认故意失败的练习仍报出文档说明的异常。

## 查看 Python 覆盖率

```bash
python3 -m pytest -q --require-xspcomm \
  --cov=xreactor --cov-branch \
  --cov-report=json:output/python-coverage/coverage.json \
  --cov-report=html:output/python-coverage/html
```

这测量的是框架的 Python 代码，不是外部 DUT 的 RTL。
行覆盖率和分支覆盖率分别查看；不要把 pytest 显示的综合百分比当成行覆盖率。
补用例时先判断遗漏了什么行为，不要直接调用私有函数或扩大排除规则来凑满数字。

真实 RTL 的构建和测试见 `examples/integration/` 中的项目说明。
原生时钟替身测试通过，不代表那些 RTL 已经重新运行。
