# 版本、CI 与发布

XReactor 的版本来自 Git tag，正式版本使用 `vX.Y.Z`。维护者不需要在源码中重复修改
版本字符串：`v0.1.0` 构建为 `0.1.0`，后续未打 tag 的提交构建为开发版本。
`setuptools-scm` 只参与构建，使用已安装的包不需要 Git 或该工具。

## 提交与版本选择

向 `main` 提交 PR 时，选择且只选择一个标签：

| 标签 | 适用变化 | 从 0.1.0 开始的下一版本 |
| --- | --- | --- |
| `release:patch` | 修复、文档或不改变公共契约的维护 | 0.1.1 |
| `release:minor` | 新增公共能力，保留已有调用方式 | 0.2.0 |
| `release:major` | 移除接口或改变已有语义 | 1.0.0 |

没有正式 tag 时从 `0.0.0` 计算增量；首个功能 PR 使用 `release:minor`，产生
`v0.1.0`。标签缺失或同时选择多个增量会使版本检查失败。默认分支是 `main`，
与 Picker、xcomm 当前使用的 `master` 分开配置。

PR 合并后，Version policy 工作流确认目标提交是 `main` 第一父链上的实际 PR 合并
结果，再创建带注释的 tag。分支中的 tag、PR 内部提交和直接 push 不触发自动发布。
重复执行同一提交时复用已有 tag；若最新版本已属于更晚的提交，拒绝给旧提交追加新版本。
版本创建任务排队执行，避免多个 PR 同时选择相同版本。
队列使用 GitHub 的 `queue: max`，避免连续合并时只保留一个待执行任务；
语义见 [GitHub 并发队列](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)。

## CI 检查什么

| 检查 | 验收条件 |
| --- | --- |
| Python 3.11、3.12 | 安装包后执行完整回归，`--require-xspcomm` 不允许缺少 native 后端时跳过；同时运行文档中的 CLI 示例 |
| native 依赖 | 从 `.github/native-dependencies.json` 读取完整提交 SHA 和预览包版本，构建 xcomm 并分别核对原生 ABI 与 coverage 描述协议 |
| 用户文档 | `mkdocs build --strict` 通过 |
| 分发包 | wheel 与源码包的名称、版本、报告模板一致，`twine check --strict` 通过 |
| 源码包重建 | 在没有 Git 元数据的目录中重新构建 wheel，保留同一版本 |
| 隔离安装 | 在新 venv 中安装 wheel，`pip check`、包导入和模板读取通过 |

原生测试使用真实 XClock，但默认 CI 不构建 Cache、e203 等外部 RTL。这些 DUT 的
构建、行覆盖和功能检查按各项目 README 单独运行；框架 CI 通过不等于 DUT 功能
覆盖关闭。已知 DUT 缺陷的复现结果仍应单独记录。

固定依赖的提交负责可重复验证，包版本负责 XReactor 的用户接口。更新 xcomm 时，
在同一个 PR 更新 pin、能力要求及相关回归。Picker 是实际 DUT 的生成工具，不是
纯 Python 包的运行依赖；项目生成的 DUT 仍需与它使用的 xcomm runtime 一致。

## 发布使用哪个构建

Tagged release 接受 tag push、Version policy 的 dispatch 或维护者手动指定的 tag。
它重新检查 tag 与合并结果的关系，再调用同一份 CI，构建并验证该提交的包。
全部检查完成后才发布 GitHub Release，上传这次 CI 的原始产物，不在发布任务重建。
发布说明由 GitHub 根据已合并的 PR 生成，因此 PR 标题应描述用户能观察到的变化。

发布包包括：

- 一个纯 Python wheel 和一个源码包；
- `RELEASE-MANIFEST.json`：包版本、源码 SHA、CI run ID、native 依赖 pin、各包的 SHA-256；
- `SHA256SUMS`：分发包和清单的校验和。

发布前再次核对清单、当前 tag、依赖、run ID 和文件集合。已有发布资产不覆盖；
若已有文件与本次验证产物完全一致，可以补齐缺失资产；校验和不同则拒绝混合发布。
发布中断时优先只重跑失败的发布 job，复用原 CI artifact。
当前流程发布到 GitHub Releases，PyPI 发布尚未配置。
普通 CI 也保留分发包 artifact，便于提交评审；覆盖数据库、日志和源码覆盖 HTML
继续作为本地产物，不上传。

Version policy 使用 `repository_dispatch` 启动后续验证，因为由 `GITHUB_TOKEN`
创建的 tag push 不会自动启动其他工作流，而 dispatch 支持这种调用。
见 [GitHub 触发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。

## 首次启用与失败恢复

仓库管理员先创建三个 `release:*` 标签，并将 `CI / test` 的两个 Python job、
`CI / docs`、`CI / package` 和 `Version policy / release-label` 设置为 PR 合并检查。
启用 GitHub Actions，并允许版本和发布工作流使用各自声明的写权限。
具体 required check 名称以首次 Actions 运行显示的名称为准。

先通过 PR 将这些文件合入默认分支，dispatch 才能找到发布工作流。如果首次合并后
dispatch 未启动，或创建 tag 后 dispatch 失败，可在 Version policy 选择 `main`、
填写原合并提交的完整 SHA 和同一增量重试；已有 tag 会被复用。
若 tag 已存在但验证或发布失败，在 Tagged release 手动填写该 tag 重跑。
手动操作也必须通过合并来源和完整 CI 检查。

本地提交前运行：

```bash
python3 -m pip install -e '.[test,docs]' 'build>=1,<2' 'twine>=6,<7'
python3 -m pytest -q --require-xspcomm
mkdocs build --strict
python3 -m build
python3 -m twine check --strict dist/*.whl dist/*.tar.gz
python3 scripts/release/verify_dist.py --manifest --verify-manifest
git diff --check
```

读取安装版本使用 `importlib.metadata.version("xreactor")`。发布时 CI 必须具有完整
Git 历史；源码包已经包含构建时确定的版本，在解包后重建不依赖原仓库。
没有 Git 和源码包元数据的普通源码副本才使用 `0.1.0.dev0` fallback。
配置依据见 [setuptools-scm 文档](https://setuptools-scm.readthedocs.io/en/v9.2.0/usage/)。
