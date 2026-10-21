# Coverage v2 实验分支

状态：实验实现，API 尚未冻结。分支为 `coverage-v2`，独立仓库位于 `/tmp/xreactor-coverage-v2-ob2uhbua/xreactor`。当前 `/home/xyl/xreactor` 保持原样；实验仓库包含复制时已有的工作区改动，本轮新增实现集中在 `xreactor.declarative`。

这版实现以长期维护为优先目标：覆盖模型用明确的类属性和对象引用组织，类型检查与定义编译共同校验，展开模型和变更对比帮助审核最终覆盖空间。声明前端拥有一个现有 coverage 引擎实例，Python 和 C++ 继续使用原有匹配、计数和报告语义。

## 基本定义

```python
from dataclasses import dataclass
from xreactor.declarative import Bin, CoverPoint, CoverGroup, Cross, coverpoint, covergroup

@dataclass(frozen=True)
class Sample:
    state: int
    taken: bool

@coverpoint
class StatePoint(CoverPoint[int]):
    idle = Bin.values(0)
    busy = Bin.values(1, at_least=2)
    done = Bin.values(2)
    path = Bin.transition(0, 1, 2)

@coverpoint
class TakenPoint(CoverPoint[bool]):
    yes = Bin.values(True)
    no = Bin.values(False)

@covergroup(schema_id="bpu.pipeline", contract="bpu.observed-pipeline/v1")
class PipelineCoverage(CoverGroup[Sample]):
    state = StatePoint()
    taken = TakenPoint()
    state_taken = Cross(state, taken)

coverage = PipelineCoverage(instance="core0.bpu")
# Monitor 生成 observed: Sample 后：
# coverage.sample(observed)
busy_count = coverage.state.count(StatePoint.busy)
```

Sample 描述观测数据，Point 集中描述 bins，Group 列出 points 与 Cross。Enum 完全可选。类型和分桶定义可以复用，每个模型实例的计数及 transition 历史独立。

同名绑定默认把 `state` 绑定到 `Sample.state`，定义编译会检查字段与类型。需要显式字段引用时，使用 `Fields(Sample).select(lambda s: s.state)`；嵌套字段也支持。selector 在定义时执行一次并记录字段路径，采样时按编译好的类型计划校验样本。

运行时按真实字段类型校验整个样本，包括 gate 关闭时的输入。int 字段拒绝 bool，Enum 字段拒绝其他 Enum 或裸值；错误输入不会修改计数或历史。illegal bin 命中沿用现有引擎的提交后报错或记录策略。结构合法的样本仍须来自项目的真实观测流水线。

## 继承与组合

Point 子类可以增加 bins 或显式覆盖同名 bin；Group 子类可以用原 Point 的子类替换槽位。继承的 Cross 按最终槽位重新展开，新 bins 会进入新组合，基类及其运行实例保持原定义。

可通过显式 `CoverageFragment[Sample]` 组织业务片段。片段必须使用同一个 Sample；共同祖先只收集一次，独立来源的重名需要在最终 Group 明确处理。具体 Group 保留一条继承链。

编译后修改类属性、样本注解或分桶声明，再次编译或实例化时会触发 `E_FROZEN`；已有实例继续依据冻结的 schema 运行。即使替换为匹配内容相同的新 Bin 对象，也要求重新定义子类，保持声明引用与编译结果一致。

## 维护和审核入口

```python
compiled = PipelineCoverage.compile()
print(compiled.explain())
print(compiled.schema_json())
print(compiled.input_contract_json())

# changes = compiled.diff(DerivedPipelineCoverage.compile())
```

`explain()` 展示最终字段、类型、bins、阈值、gate、Cross 组合数量及声明来源。`diff()` 比较 schema 与输入契约，列出新增、删除和变化的项目，并包含展开后的 Cross 组合。来源位置不进入差异比较。

实例绑定后，`binding_contract_json()` 展示实际字段、位宽、稳定 source_id 和不透明来源，便于审核接线。源码位置用于辅助定位，不参与 schema 指纹；样本类型的模块及限定名参与输入契约，因此类型重命名可能导致历史报告不再可合并。

## 接入 C++ 引擎

```python
from xreactor import Execution, RisingEdge
from xreactor.declarative import Fields, wire

fields = Fields(Sample)
coverage.bind(
    trigger=RisingEdge(clock),
    fields=(
        wire(fields.select(lambda s: s.state), dut.state, source_id="dut.state"),
        wire(fields.select(lambda s: s.taken), dut.taken, source_id="dut.taken"),
    ),
    strategy="native",
)
# async with Execution(backend, coverage=[coverage.runtime]):
#     ...
```

`coverage.runtime` 是唯一保存计数的现有 CoverGroup 实例，也用于 `CoverageDatabase`。声明对象通过显式方法访问它，避免类型化接口继承低层 API 的不同方法签名。高频 native 采样直接读取 XData，不构造 Python Sample，也不进入 Python 事务校验路径。

clock-bound 输入目前限定 int/bool，bool 要求一个物理 bit。已有 adapter 支持 values、区间、宽 mask、相邻 transition、Cross 与 gate。严格 native 在能力不足时拒绝；auto 整组回退并在报告中记录原因。两种时钟后端使用相同绑定契约。

未提供稳定 source_id 时，需要显式 observer contract。项目应在观测含义改变时更新其版本；contract 无法自动发现项目在未更新版本的情况下换绑了不透明来源。

## 本机运行

在实验仓库根目录执行，最初使用本机已有的 xcomm coverage 描述协议 3 构建：

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src:/tmp/xreactor-coverage-native-build/python"

python3 -B examples/coverage/declarative_pipeline.py --backend manual
python3 -B examples/coverage/declarative_pipeline.py --backend native \
  --output-dir /tmp/xreactor-coverage-v2-ob2uhbua/artifacts/native

python3 -B -m pytest tests -q -p no:cacheprovider --require-xspcomm
python3 -B scripts/check_coverage_v2_types.py \
  --pyright node /tmp/xreactor-class-coverage-tools/node_modules/pyright/index.js
```

完整示例见 [declarative_pipeline.py](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/examples/coverage/declarative_pipeline.py)。公开 API 在 [declarative 包](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/src/xreactor/declarative/__init__.py)，行为验证在 [test_declarative.py](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/tests/coverage/test_declarative.py)。

多周期 coverpoint、类型化引脚组、Sequence 完成时采样及动态 tag 事务关联见[协议绑定示例](coverage-v2-protocol.md)，可运行代码为 [declarative_protocol.py](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/examples/coverage/declarative_protocol.py)。它从实际稳定引脚和 Monitor 快照生成覆盖样本。

## 初版验证

初版提交 `93d3ba7`，2026-10-08，本机 Python 3.12.3、xcomm coverage 描述协议 3：

- 完整 `tests` 目录 **922 passed**，包含 **37 个**新增声明层行为用例。
- Pyright 1.1.414、Python 3.11 类型目标：公开 API 严格正例无错误，9 个标记负例全部拒绝。
- 基础模型和派生模型的 manual、Python 时钟采样、native 统计一致；嵌套字段、gate、128 位 mask 及 transition 的统计也一致。
- native 采样没有进入 Python 事务校验路径；严格 native 拒绝和 auto 回退均验证了注册清理。
- 实验 wheel 包含新模块与 `py.typed`，从独立解包目录运行导入和严格类型检查均成功。实验包使用显式版本 `0.1.0.dev0`，未发布或安装到全局环境。

审核产物在 `/tmp/xreactor-coverage-v2-ob2uhbua/artifacts/`，按 manual、python、native 分目录保存。原仓库基线记录的 236 个文件指纹保持不变。

## 本次协议示例验证

2026-10-08，在同一 Python/ABI 环境中补上协议绑定实例：

- 完整 `tests` 目录 **944 passed**，本次新增 **22 个**协议与采样契约行为用例。
- 两份公开 API 严格类型正例通过，12 个标记负例全部拒绝。
- memory/Python、xcomm/Python、xcomm/native 三条路径的请求周期覆盖、Sequence 完成采样和事务覆盖统计一致；同时核对窗口超时、错误数据、丢响应、tag 复用和资源清理。
- 采样契约纳入 Sequence/Within 程序形状及窗口参数，不同窗口的报告拒绝合并。
- 重新构建 wheel；独立解包后的 native 协议示例和两份严格类型正例通过。

本次产物位于 `/tmp/xreactor-coverage-v2-ob2uhbua/artifacts/protocol-{memory,python,native,window2}/`。完整测试记录为 `/tmp/xreactor-coverage-v2-ob2uhbua/protocol-full-tests.txt`。

随后 Bundle 复用修订将运行示例直接改为已有 Bundle 子类，并让公共 signal_expr 原样接受已有符号节点。完整测试 **948 passed**，包含四个 memory/native、symbolic/bound 的表达式构造回归用例；原有三种协议执行路径仍通过。既有严格类型正例和 12 个负例也通过。记录位于 `/tmp/xreactor-coverage-v2-ob2uhbua/coverage-reuse-full-tests.txt`；完整 pattern bin 的新 native 能力仍未实现。

## 共享时序程序的设计方向

后续设计更新见[共享时序程序与 C++ 执行草稿](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/design/drafts/coverage-v2-patterns.md)。目标是复用 xtrigger 的 Expr/Sequence/FSM 定义，让 trigger 通知、coverage 持续计数和观测记录各自保存运行状态；每个 bin 的完整模式、字段捕获、按 key 关联和完成上下文都可以向 C++ 引擎提出能力需求。

当前优先范围是长 Execution 内的程序回收、每个 bin 的完整时序程序，以及表达式类型语义。程序与观察实例的拆分程度由实现需要决定；统一诊断和规模优化后排，字段捕获与动态 key 关联继续讨论。

下一步分支修改见[具体接口与分批实施方案](https://github.com/Makiras/xreactor/blob/draft/coverage-v2/design/drafts/trigger-bins/README.md)，附有可严格类型检查的接口草稿。普通 bin 直接引用 xtrigger 定义，需要阈值等配置时才使用 Bin.pattern；参数绑定、重复引用、继承、采样和终态选择都有明确规则。

复用修订明确使用现有 Bundle/ReadyValid、signal_expr、Expr/Sequence/FSM、CompiledGroup 和现有 coverage runtime；协议运行示例也直接使用 Bundle 子类。新增能力只补已有组件的缺口，不建立另一套信号容器、时序 IR 或计数报告系统。

初版验证时 `Bin.pattern`、`TemporalCoverPoint`、`SignalCoverGroup` 尚未实现。当前分支已经接通这些能力，支持每个 bin 的完整程序及同 point 下共享执行；最新接口与配套引擎要求见 [trigger bins 指南](coverage-v2-trigger-bins.md)。

## 当前边界

这轮完成了基础声明、装饰器、类型化字段路径、继承和片段、模型审核、运行绑定及现有 native engine 接入。候选设计中的 `plan(backend)`、强类型 Enum 信号解码、任意 extractor、复杂 bin 时序 DSL 和编辑器交互体验仍未完成。

Python 的 bool 是 int 的子类型，静态工具在某些 lambda 上下文中会把 bool 返回值提升为 int；定义编译仍会检查真实字段注解并拒绝错误绑定。默认同名绑定不能保证 IDE 同时重命名两个类中的属性。selector 接受普通字段路径，符号代理不构成执行任意 Python 定义代码的安全沙箱。

测试和本机 ABI 验证可以支持这条实现路线，不能作为完整候选 API、所有编辑器体验或真实 DUT 性能的验收结论。


2026-10-09 已接入 coverage 描述协议 4 的每 bin 完整程序实验，并适配原生 ABI 3 的 picker/xcomm `xreactor-coverage-v2` 分支。直接使用 @xtrigger 定义 bins、共享 Sequence/FSM 推进、原生持续计数的用法见 [trigger bins 指南](coverage-v2-trigger-bins.md)。后续草稿已补长 Execution 程序回收和两态 typed 表达式，详见[实现与实测](coverage-v2-engine-audit.md)；X/Z 处理暂缓。
