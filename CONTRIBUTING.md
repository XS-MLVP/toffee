# 参与 Toffee 开发

Toffee 负责 Python API、仿真调度、验证组件和报告。
原生时钟、信号和触发引擎由 xcomm 提供；真实 DUT 的环境代码放在
`examples/integration/<dut>/`，不要混进框架实现。

## 一项改动应包含什么

新增或修改功能时，一并更新：

1. `src/toffee/` 中的实现。
2. `docs/guides/` 或 `docs/reference/` 中的用法和限制。
3. 对应 `tests/` 分类中的回归。
4. 面向用户的功能示例；影响高频路径时，还应有可重复的性能测试。

文档说明当前能做什么、怎样使用。不要把试验过程、机器专用路径、分轮测试数字
或未实现的接口原型放进用户手册。生成的报告、日志和测量结果写入 `output/`，不要提交。

## 提交前检查

先安装开发依赖：

```bash
python3 -m pip install -e '.[test,docs]'
python3 -m pytest -q --require-xspcomm
mkdocs build --strict
```

`--require-xspcomm` 会在原生依赖不满足时失败，避免把原生测试没运行当成通过。
没有原生环境时，可先用 `python3 -m pytest -q` 检查纯 Python 部分，但这不代替完整回归。

原生后端的提交、包版本、ABI 和协议版本以
[依赖清单](.github/native-dependencies.json)为准。
只编译了本地绑定而未安装时，可设置 `XCOMM_PYTHON=/path/to/xcomm/build/python`。
测试也会查找同级 Picker 的 `build/dependence/xcomm/python`。

需要从 xcomm 源码安装时，SWIG 须为 4.2 或更新版本，并打开两个构建选项：

```bash
BUILD_XSPCOMM_SWIG=python XSPCOMM_BUILD_WHEEL=1 \
  python3 -m pip install /path/to/xcomm
```

不要只升级 Python 包而混用旧原生库。CI 会安装依赖清单固定的版本并运行原生测试。

## 生命周期

`Execution` 只清理本次执行注册的等待、采样和组件，不拥有 backend。
创建 backend 的代码负责调用 `close()`；创建 DUT 的 fixture 负责初始化和释放 DUT。
修改这类行为时，要同时测试正常退出、取消和异常退出。

## 版本和发布

版本号由 Git tag 和 setuptools-scm 生成，不在源码中另写一份。
针对 main 的 PR 需选择一个版本标签：`release:patch`、`release:minor` 或 `release:major`。
具体选择和发布失败后的处理见[版本与发布](docs/guides/versioning-and-releases.md)。

发布检查包括原生回归、严格文档构建、源码包重建和隔离安装。
验证通过后发布的是同一组已检查产物，不另行构建一份替代。
