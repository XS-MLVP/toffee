# 参与 Toffee 开发

Toffee 负责 Python API、仿真调度、验证组件和报告。
原生时钟、信号和触发引擎由 xcomm 提供；真实 DUT 的环境代码放在
`examples/integration/<dut>/`，不要混进框架实现。

## 一项改动应包含什么

新增或修改功能时，一并更新：

1. `src/toffee/` 中的实现。
2. 相应的用户文档；按下文的读者目的选择页面。
3. 对应 `tests/` 分类中的回归。
4. 面向用户的功能示例；影响高频路径时，还应有可重复的性能测试。

文档说明当前能做什么、怎样使用。不要把试验过程、机器专用路径、分轮测试数字
或未实现的接口原型放进用户手册。生成的报告、日志和测量结果写入 `output/`，不要提交。

## 文档按读者目的组织

文档采用 [Diátaxis](https://diataxis.fr/) 的四类划分：

| 类型 | 位置 | 写作要求 |
| --- | --- | --- |
| 教程 | `docs/getting-started/` | 带读者完成可复现的练习，给出命令与预期结果；逐步引入概念，减少分支选择 |
| 操作指南 | `docs/guides/` | 开头说明目标和前提，围绕实际任务给步骤、验证方法和常见失败处理 |
| 参考 | `docs/reference/` | 准确列出签名、默认值、单位、返回值、异常与限制；与实现逐项核对 |
| 原理说明 | `docs/explanation/` | 解释原因、边界和取舍，通过具体场景建立概念之间的联系 |

一页主要解决一种阅读需求；需要其他类型的内容时链接过去，避免在操作步骤中展开长篇
原理，或在教程中罗列所有参数。已有页面混合多种内容时逐步拆分，并尽量保留旧地址。
例如，配置步骤留在 Execution 指南，默认值放在 Execution 参考，调度原因放在原理说明。

新增页面须接入 `mkdocs.yml` 和相应目录入口。检查相对链接与页内锚点，
运行 `python3 -m pytest -q tests/integration/test_documentation.py` 和 `mkdocs build --strict`。
新增可运行示例时还要实际执行；已有覆盖模型片段由文档测试校验。

## Wiki 自动同步

Wiki 的内容来自 `main` 的 `docs/`，导航来自 `mkdocs.yml`。只需编辑这些源文件。
**Documentation** 工作流校验链接、构建网站，导出页面及侧边栏，并上传预览产物。
文档修改进入 `main` 并通过检查后，会同步到 `toffee.wiki.git`；PR 和其他分支只生成预览。
Legacy `master` 不参与同步。Wiki 工作流独立于软件包发布检查，避免扩大其写权限。

首次启用时，须在仓库设置中开启 Wiki，并在 GitHub Wiki 网页创建一次首页。
同步使用自带的 `GITHUB_TOKEN`，只有发布任务拥有 `contents: write`，无需额外 secret。
失败后可在 Actions 中选择 `main` 手动运行 **Documentation**。新文档已提交时，旧导出会跳过发布。

本地验证和导出不需要安装原生依赖：

```bash
python3 -m pip install 'mkdocs>=1.6,<2'
python3 -m unittest discover -s scripts/tests -v
python3 scripts/docs.py check
python3 -m mkdocs build --strict
python3 scripts/docs.py wiki --output build/wiki-preview
```

导出目录须为空。页面、源码和图片链接转换为绝对 URL，代码示例保持原样。
`manifest.json` 记录每个生成页面的 SHA-256。同步会更新生成页面，首次运行时替换初始化
首页、侧边栏和页脚；其他手工内容会保留，同名冲突会报错。过期页面只有在仍符合旧清单时
才被删除，存在手工改动时停止供检查。内容不变时不创建提交，推送不会强制覆盖历史。
请将生成页面的修改提交到主仓库，避免下一次自动同步覆盖网页上的手工编辑。

本地检查同步差异时，先克隆已初始化的 Wiki，再执行：

```bash
git clone https://github.com/XS-MLVP/toffee.wiki.git build/wiki-checkout
python3 scripts/sync_wiki.py --export build/wiki-preview --checkout build/wiki-checkout
git -C build/wiki-checkout diff
```

## 提交前检查

先准备 `uc-xcomm>=1.1,<2`，再安装开发依赖。开发跨仓库未发布修改时，
按 `.github/native-dependencies.json` 检出 xcomm 的固定提交，再运行：

```bash
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_UC_XCOMM=1.1.0 \
  python3 -m pip install /path/to/xcomm
```

这里的版本覆盖仅用于本地和 CI 验证目标依赖范围，不发布该测试包。随后安装开发依赖：

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
