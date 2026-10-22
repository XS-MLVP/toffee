# 0. 准备一个能运行例子的环境

这一页完成后，你应该能在终端导入 Toffee。暂时不需要安装硬件仿真器。
下面使用 Linux/macOS 的 shell；Python 需要 **3.11 或更新版本**。

## 找到仓库根目录

如果你已经拿到了源码，进入包含 `pyproject.toml`、`examples/`、`docs/` 的 `toffee` 目录。
如果还没有源码，在终端执行：

```bash
git clone --branch main https://github.com/XS-MLVP/toffee.git
cd toffee
```

以下所有命令都在这个目录运行。不要进入 `src/` 或 `examples/` 再执行。

分发包名是 `pytoffee`，Python 导入名是 `toffee`。Toffee 2 当前从源码安装；
PyPI 上的旧包不等同于这条新主线。需要固定版本时可检出 `v2.0.0` 标签。

## 安装到独立的 Python 环境

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -c 'import toffee; print(toffee.__file__)'
```

最后一条应打印本仓库 `src/toffee/__init__.py` 的路径。

这里 `venv` 创建名为 `.venv` 的独立环境；`source` 让当前终端使用它。`pip install`
安装本项目和测试依赖；`-e` 让源码修改立即生效。`.[test]` 两边的引号要保留。
以后打开新终端，进入仓库后重新执行 `source .venv/bin/activate` 即可。

测试依赖里的 pytest 负责找到并运行测试，pytest-asyncio 负责运行后面需要等待时钟的
`async def` 测试。你不必自己创建 event loop 或 TaskGroup。

## 安装时卡住了

| 现象 | 先检查什么 |
| --- | --- |
| Python 版本低于 3.11 | 安装较新版本，用它重新创建 `.venv` |
| `No module named venv` 或缺少 `ensurepip` | 安装系统对应的 Python venv 组件后重试 |
| pip 下载失败 | 检查网络或已有的包镜像配置；这还没有运行任何仿真 |
| `No module named toffee` | 确认激活了环境，并用同一个 `python3` 执行安装和运行命令 |
| 后续提示 `No module named examples` / `file not found` | 回到仓库根目录，使用文档中的 `python3 -m pytest ...` |

先继续 [1. 第一次 PASS 和 FAIL](first-test.md)。
需要原生 XClock 时再看 [接入 native](native-and-next.md)，不必在这里被 绑定 构建挡住。
