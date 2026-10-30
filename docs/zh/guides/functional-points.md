# 功能点、场景与跨用例覆盖

功能点来自设计要求，case 提供激励。两者是多对多关系：同一个功能点可以由不同 case、
不同 seed 共同覆盖，一个 case 也可以贡献多个功能点。Toffee 不要求给测试声明功能
标签；覆盖由环境中的实际采样产生，pytest 负责测试执行与通过/失败结果。

## 从要求到可观测场景

先定义要验证的行为、触发条件、采样位置与判断依据，再设计激励。例如 Cache refill：

| 功能要求 | 需要实际出现的场景 | 判断依据 |
| --- | --- | --- |
| 从请求 word 开始填充 | 八种起始 word | 已接受请求的地址与首拍阵列写入地址 |
| 接受才推进计数 | 接受、响应空拍、7→0 回绕 | 接受事件账本与前后计数、写地址 |
| 末拍才提交 metadata | 中间拍、末拍前空拍、末拍接受 | metadata 写使能与新 tag/valid |

场景可以实现为 CoverPoint 的 bins、Cross，或项目观察器识别出的时序事件。是否命中由
实际事务或状态决定，不能用 case 名称、随机约束或“设置过 stall 参数”代替观察。
不同来源的数据应可区分，检查使用独立模型或事件账本，避免复制 RTL 方程作为唯一判据。

覆盖采样与正确性检查各有职责：bin 命中记录条件曾经发生；assertion / Scoreboard
检查发生后的行为。条件从未触发而没有报错，不算验证完成；已经触发但检查失败，也不能
因覆盖率达标而忽略。应明确采样发生在输入接受、DUT 输出观察还是比较成功之后。

## 在验证环境中统一采样

采集器应由环境或 Agent 的观察路径接入，让适用的 case 共用。用例只负责配置和产生
激励，不需要逐一列出自己“负责”的功能点。内部机制可以由项目的逐周期观察器采样；
通用 Driver 和调度器无需知道协议含义。

已有运行配方在 Monitor 交付记录时采样，由同一条观察继续送入 Scoreboard，避免多个
消费者争抢事务。见[组件组合与运行产物](verification-flow.md)。检查失败时已有观察仍
会保存在该次运行的覆盖文件中，运行失败状态另行保留。

同一个时序场景必须在一次运行中完整观察。例如“两个请求同时竞争”，不能用一次运行
看到请求 A、另一次看到请求 B 来合成命中。Cross 也合并已实际命中的组合，不对不同运行
的单维命中做笛卡尔积。

## 多个 case 共同贡献覆盖

可运行示例 [test_pipeline_coverage.py](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/test_pipeline_coverage.py)
使用同一个环境和同一个 `pipeline.output` 覆盖实例：

| case | 实际观测到的响应 tag | 单次覆盖 |
| --- | --- | --- |
| first，seed 17 | 1、2 | 2/3 |
| second，seed 23 | 2、3 | 2/3 |
| 两次运行合并 | 1、2、3 | 3/3 |

```bash
python3 -m pytest -q -s examples/transactions/test_pipeline_coverage.py
```

两个 case 都应通过，不要求各自达到全局 100%。每次运行打印自己的产物目录；将两份
`functional.json` 传给同一个报告命令，下面的 run-a/run-b 替换为实际目录：

```bash
toffee-coverage-report \
  --functional run-a/functional.json \
  --functional run-b/functional.json \
  --output output/coverage.html --site output/coverage-site
```

合并后 tag 2 的次数为 2，但它仍只占一个 bin。默认 `at_least=1` 时，覆盖集合是各次
运行命中集合的并集；如果要求命中 N 次，则先累计计数，再判断是否达到 N。不要平均
各 case 的百分比，也不要把测试数量当成覆盖率分母。

Python 中可以在汇总阶段检查目标：

```python
from toffee import CoverageDatabase

combined = CoverageDatabase.read_json("run-a/functional.json")
combined.merge(CoverageDatabase.read_json("run-b/functional.json"))
combined["pipeline.output"].assert_coverage(100.0)
combined.write_json("output/combined.json")
```

跨 case 合并同一个 DUT 位置时使用稳定的实例名；不要把 case 名或 seed 拼进覆盖实例名。
不同 DUT 实例仍分别保留，按类型汇总需显式使用 `type_reports()`。同一实例 schema 不兼容
会报错；RTL 版本、配置和输入文件是否属于同一批回归由项目控制，目前不自动验证这些身份，
重复或重叠采集来源会被拒绝，防止重复文件和同一运行的累计快照增加命中数。
运行目录、seed、nodeid 和运行状态用于定位证据，不决定 bin 命中。

运行配方通过 instantiate 的 run_id、run_metadata 和 contract 保存独立运行身份、
测试名/seed 以及采样位置。合并后的每个普通 bin 都保留各来源的命中数和首次/末次
metadata，报告可直接回答哪些运行贡献了命中；原生计数没有实际命中位置时
不会补造时间。不要用 seed 单独标识一次运行，也不要在 sample metadata 中仅写 run_id
来代替数据库的运行身份。报告格式和验收策略见[覆盖率指南](coverage.md)。

## 根据汇总缺口调整 case

| 发现 | 下一步 |
| --- | --- |
| 已有相关激励，尚无对应采样器 | 补观察，实测后再判断命中；不要报成 0% |
| 已有采样器，合法场景命中为零 | 调整约束或添加定向序列，核对可达性 |
| 当前串行激励无法建立冲突条件 | 增加项目级重叠输入、资源竞争或模块级场景 |
| 已命中，但检查失败 | 保留覆盖与失败证据，定位问题 |
| 本次测试跳过或未运行 | 在 pytest 结果中记录；不撤销其他运行已有的覆盖贡献 |

验收目标应施加在选定回归的合并结果上。针对某个定向 case，可以额外检查它确实触发了
预定场景；这种检查服务于激励有效性，不要求每个 case 独立完成整个覆盖模型。

## 项目计划保留什么

[e203](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/e203/verification_plan.json)
和 [Cache](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/cache/verification_plan.json)
的计划保留 `scope` 与 `requirements`：设计范围、源码基线、稳定 ID、行为要求、观察点、
必需场景和检查依据。它们是设计评审材料，不是 pytest 输入，不按测试标签生成完成率。
现有检查代码的位置可以作为阅读线索，但不决定覆盖归属。

内部义务包括状态转移、寄存器装载/保持、forwarding、仲裁、计数器、提交与退休等。
e203 的 49 个、Cache 的 77 个内部义务已接入项目属性观察器；运行产生的
`internal-functional-coverage.json` 记录实际激活，`internal-checks.json` 单独记录比较和关闭状态。
没有采样的场景属于未测量；场景命中不能替代属性检查。报告和运行证据保存在本地，
不纳入 Git。具体契约见上述验证计划及项目检查代码。

更细的 bins、门控、cross 与报告 API 见[覆盖率指南](coverage.md)。
