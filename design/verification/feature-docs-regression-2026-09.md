# 按功能重组文档与回归审计

> 后续变更：人工 pytest 功能标签、对应插件和完成率报告已移除。本文相关数字和
> 插件行为仅保留为当时的审查记录，不代表当前功能覆盖。运行命令与当前使用方式见
> [移除记录](coverage-without-test-tags-2026-09.md)及各项目 README。

日期：2026-09-29。目标是重新梳理用户文档及回归，覆盖主要功能的使用场景，并精简
重复实现与测试。该轮以现有公开 API、实际行为和可运行例子为准，没有新增调度模型或
更改 Driver 协议、SingleCycle idle 策略。

## 要求与证据

| 要求 | 当前实现与验证 |
| --- | --- |
| 按 feature 组织文档 | mkdocs 导航分为快速开始、仿真/事件、验证组件、覆盖率/运行、场景和参考；主页以使用任务连接功能 |
| Driver/Monitor/Agent/Scoreboard 的使用 | 独立功能页，包含类型选择、构造/调用、接受/采样、模型/期望、关联、预算、退出与失败；功能索引连接对应源代码回归 |
| quick start 可运行 | counter 与 Agent fixture 两条路线进入默认 pytest；从 Markdown 原文提取到临时目录的两个测试也通过 |
| 不遗漏既有公共能力 | 对照 __all__ 的 125 个导出，API 索引全部收录；15 个功能族在 feature-map 关联主要场景和行为回归，低层声明/句柄单列供组件开发者查询 |
| 补齐场景和行为边界 | 补充 Field/bind_tree、packed 位序/空洞、被动 Monitor、Sync/Async 连接、callable idle、覆盖率门控/cross/目标/reset、pytest 中断与报告集成 |
| 精简代码 | 清理错误展开、去重及原始异常保留集中到私有 _cleanup_errors；Agent/Execution 不再各写一套，Driver/Monitor/Scoreboard 复用同一逻辑 |
| 精简测试 | 双后端时钟与 ToyPipeline fixture 各保留一份；Monitor 的关闭、交付容量、单等待取消与异步迭代用共同参数化契约，保留协议候选/采样专属回归 |
| 功能实际得到验证 | 强制 native 全量、真实 genhtml 源码页、两种后端的模式边界/Driver idle、CLI 与生成报告均运行；不只检查文案或文件是否存在 |

用户入口：[主页](../../docs/index.md)、[功能与回归索引](../../docs/reference/feature-map.md)、
[Agent quick start](../../docs/getting-started/agent-test.md)。

## 审计中发现并处理的问题

- 主页没有 Agent 入口，Driver/Monitor 被埋在数据布局页。拆为 data/drivers/monitors，
  可选协议独立成页，编译模式与持久订阅分开；旧混合页移除，内部链接同步更新。
- 新增 quick start 与已有 test_agent 同名时，pytest 默认导入发生冲突；示例采用唯一
  文件名 test_agent_quickstart.py，保留原有 pytest 导入语义。
- Hold 中断只清零该步骤的保持计数；Within 超出窗口才回到 Sequence 开始。此前
  “重启匹配”的表述不够精确。两种后端用同一组采样表验证上下界、过早命中、超时重启、
  Hold 间断及 FSM 同拍多个分支的先后优先级。
- callable idle 的 None 参数发生在启动及显式 quiesce；关闭不自动重写最后 idle。
  新回归核对带/不带 quiesce 的实际引脚值与调用参数，原驱动行为保持不变。
- 单个源码文件的 LCOV 报告在 genhtml 2.0-1 上实际失败：自动 --prefix 剥离了整个父
  目录。移除这段路径推断，使用工具自身的路径选择；真实单文件及嵌套源码回归均通过。
  CI 安装 lcov，防止只测试 Python 报告模型而漏掉原生工具集成。
- 覆盖率 weighted goal/reset、cross iff 缺少直接行为断言，已补充；其余主要 bins、
  schema、合并、非法命中、pytest 子进程等回归复用原有测试。

## 完整性核查方式

逐个核对顶层导出及其所属能力，阅读对应实现和现有测试的断言，再补充缺失场景。
功能表只描述回归实际证明的行为。125 个名字收录是接口导航的完整性证据，不等于
所有输入组合都已穷尽；Python snippet 能解析也不等于能独立执行，只有 quick start
明确按完整内容执行验证。

共用的关闭测试保持 backend × monitor 参数矩阵；Sync/Async Driver 的提交方式不同，
没有为了降低数量合并为同一种 Driver。回归数量增长来自新增行为边界和用户示例，
精简针对重复控制流/fixture，不以减少断言或删除异常路径为目标。

## 验证结果

环境：Python 3.12.3，`/tmp/xreactor-verification-venv`，native 模块来自同级 Picker 构建，
genhtml 为 LCOV 2.0-1。运行期间生成的临时产物在 `/tmp/xreactor-feature-audit`。

```text
python3 -m pytest -q --require-xspcomm
457 passed in 6.39s

mkdocs build --strict
passed

从 Markdown 提取两个完整 quick start 后运行 pytest
2 passed
```

12 个命令场景均 exit 0：native binding、基本 Trigger、订阅、Scoreboard、流水输入
memory/native、参考模型 memory/native、functional JSON、pytest feature JSON、单文件
综合 HTML、多页综合站点（包含真实 genhtml 源码页）。

静态/构建检查：125 个导出无漏项，63 段 Python 代码语法有效，源码链接均有对应文件，
25 个渲染页面的 1263 个内部链接及 fragment 无缺失。具体结果见
审计数据（本地产物：`feature-docs-regression-2026-09.json`）。

CI 默认 pytest 已包含两个 quick start 和 pipeline 覆盖率配方，删去单独重复调用；
命令行示例作为独立入口 smoke 保留，并加入基本 Trigger 和 functional coverage 脚本。

## 验证范围

本轮验证框架、MemoryBackend/native XClock 替身、文档与报告工具。没有重新构建 e203、
cache 等外部 RTL；其依赖与项目命令仍在对应 README。native 时钟测试不代表任意 DUT
的协议验证。跨接口同 tick 顺序、远程模型适配、多响应关联、跨时钟调度及 pytest-xdist
聚合仍以当前限制说明为准，不把设计草案当成已提供功能。
