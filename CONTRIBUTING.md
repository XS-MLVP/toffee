# 参与 Toffee 开发

Toffee 负责 Python API、仿真调度、验证组件和报告。
原生时钟、信号和触发引擎由 xcomm 提供；真实 DUT 的环境代码放在
`examples/integration/<dut>/`，与框架实现分开组织。

## 一项改动应包含什么

新增或修改功能时，一并更新：

1. `src/toffee/` 中的实现。
2. 相应的用户文档；按下文的读者目的选择页面。
3. 对应 `tests/` 分类中的回归。
4. 面向用户的功能示例；影响高频路径时，还应有可重复的性能测试。

用户文档说明当前能力和使用方法。试验过程与接口草案放在设计记录中；机器专用路径、
分轮测试数字、生成报告和日志作为本地产物保存在 `output/`。

## 模块命名

公开模块按用户概念命名，例如 `outcomes.py` 提供可复用的结果分类接口。
内部模块使用下划线前缀，并写明子系统和职责，例如 `_pytest_plugin.py` 是 pytest
加载入口，`_pytest_outcomes.py` 负责标记和终端显示，`_runner_artifacts.py` 负责运行产物。
特定工具的适配器使用工具名，如 `_verilator_coverage.py`；通用模块保持独立。
已有子包提供足够上下文时，可使用简短名称，例如 `declarative/_compiler.py`。
重命名时同步更新导入、动态插件加载、测试与文档中的路径。

## 文档按读者目的组织

中文源文档放在 `docs/zh/`，英文放在 `docs/en/`，相对路径一一对应。新增或修改
公共能力时，同一个 PR 更新两种语言；共享的可执行代码块保持一致。
导航使用两种语言共用的路径，英文标题配置在 `mkdocs.yml` 的 `nav_translations` 中。
站点默认中文，提供 English 切换；文档检查严格要求对应翻译完整。

文档采用 [Diátaxis](https://diataxis.fr/) 的四类划分；下表以中文路径为例：

| 类型 | 位置 | 写作要求 |
| --- | --- | --- |
| 教程 | `docs/zh/getting-started/` | 带读者完成可复现的练习，给出命令与预期结果；逐步引入概念，减少分支选择 |
| 操作指南 | `docs/zh/guides/` | 开头说明目标和前提，围绕实际任务给步骤、验证方法和常见失败处理 |
| 参考 | `docs/zh/reference/` | 准确列出签名、默认值、单位、返回值、异常与限制；与实现逐项核对 |
| 原理说明 | `docs/zh/explanation/` | 解释原因、边界和取舍，通过具体场景建立概念之间的联系 |

一页主要解决一种阅读需求；其他类型的内容通过链接关联。操作步骤、原理与参数参考分别组织。
已有页面混合多种内容时逐步拆分，并尽量保留旧地址。
例如，配置步骤留在 Execution 指南，默认值放在 Execution 参考，调度原因放在原理说明。

新增页面须接入 `mkdocs.yml` 和相应目录入口。检查相对链接与页内锚点，
运行 `python3 scripts/docs.py check`、
`python3 -m pytest -q tests/integration/test_documentation.py tests/integration/test_quickstart_tutorial.py`
和 `mkdocs build --strict`。
新增可运行示例时还要实际执行；已有覆盖模型片段由文档测试校验。

## Wiki 自动同步

Wiki 的内容来自 `main` 的 `docs/zh/` 和 `docs/en/`，导航来自 `mkdocs.yml`。
首页提供语言选择，页面名使用 `zh-` / `en-` 前缀，每页链接对应的另一语言版本。
只需编辑这些源文件；功能与对应的双语文档应在同一个 PR 中提交。
**Documentation** 工作流校验链接、构建网站，导出页面及侧边栏，并上传预览产物。
文档修改进入 `main` 并通过检查后，会同步到 `toffee.wiki.git`；PR 和其他分支只生成预览。
Wiki 工作流仅同步新主线，权限与软件包发布检查分别管理。

首次启用时，须在仓库设置中开启 Wiki，并在 GitHub Wiki 网页创建一次首页。
同步使用自带的 `GITHUB_TOKEN`，发布任务拥有 `contents: write`。
失败后可在 Actions 中选择 `main` 手动运行 **Documentation**。新文档已提交时，旧导出会跳过发布。

本地文档验证和导出使用以下独立依赖：

```bash
python3 -m pip install -r requirements-docs.txt
python3 -m unittest discover -s scripts/tests -v
python3 scripts/docs.py check
python3 -m mkdocs build --strict
python3 scripts/docs.py wiki --output build/wiki-preview
```

导出目录须为空。页面、源码和图片链接转换为绝对 URL，代码示例保持原样。
`manifest.json` 记录每个生成页面的 SHA-256。同步会更新生成页面，首次运行时替换初始化
首页、侧边栏和页脚；其他手工内容会保留，同名冲突会报错。过期页面只有在仍符合旧清单时
才被删除，存在手工改动时停止供检查。内容有变化时创建提交，并常规推送。
生成页面的修改统一提交到主仓库，由自动同步更新 Wiki。

本地检查同步差异时，先克隆已初始化的 Wiki，再执行：

```bash
git clone https://github.com/XS-MLVP/toffee.wiki.git build/wiki-checkout
python3 scripts/sync_wiki.py --export build/wiki-preview --checkout build/wiki-checkout
git -C build/wiki-checkout diff
```

## 提交前检查

先准备 `uc-xcomm>=2.0,<3`，再安装开发依赖。需要使用依赖清单固定的跨仓库原生依赖时，
按 `.github/native-dependencies.json` 检出 xcomm 的固定提交，再运行：

```bash
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_UC_XCOMM=2.0.0 \
  python3 -m pip install /path/to/xcomm
```

这里的版本覆盖用于本地和 CI 验证目标依赖范围。随后安装开发依赖：

```bash
python3 -m pip install -e '.[test,docs]'
python3 -m pytest -q --require-xspcomm
mkdocs build --strict
```

`--require-xspcomm` 严格检查原生依赖，并在接口缺失时报告环境错误。
纯 Python 检查可运行 `python3 -m pytest -q`；完整回归使用上面的原生依赖和检查选项。

原生后端的提交、包版本、ABI 和协议版本以
[依赖清单](.github/native-dependencies.json)为准。
使用本地编译的绑定时，可设置 `XCOMM_PYTHON=/path/to/xcomm/build/python`。
测试也会查找同级 Picker 的 `build/dependence/xcomm/python`。

需要从 xcomm 源码安装时，SWIG 须为 4.2 或更新版本，并打开两个构建选项：

```bash
BUILD_XSPCOMM_SWIG=python XSPCOMM_BUILD_WHEEL=1 \
  python3 -m pip install /path/to/xcomm
```

Python 包和原生库使用匹配版本。CI 安装依赖清单固定的版本并运行原生测试。

## 生命周期

`Execution` 清理本次执行注册的等待、采样和组件。
创建 backend 的代码负责调用 `close()`；创建 DUT 的 fixture 负责初始化和释放 DUT。
修改这类行为时，要同时测试正常退出、取消和异常退出。

## 版本和发布

版本号统一由 Git tag 和 setuptools-scm 生成。
针对 main 的 PR 需选择一个版本标签：`release:patch`、`release:minor` 或 `release:major`。
具体选择和发布失败后的处理见[版本与发布](docs/zh/guides/versioning-and-releases.md)。

发布检查包括原生回归、严格文档构建、源码包重建和隔离安装。
验证通过后直接发布同一组已检查产物。
