# 基于类声明的 functional coverage API 设计草稿

状态：**讨论稿，完整 API 未实现、未冻结**。日期：2026-10-08。同目录已增加可运行的最小 native 编译探针。文件独立保存，不修改当前 coverage 引擎、公共导出、用户指南、测试入口或构建配置，也不提出迁移要求。

推荐采用三层声明：**Bin 是不可变规则对象；CoverPoint 子类描述一个值域及其 bins；CoverGroup 子类把 points 绑定到观测样本并定义 Cross。** 类属性提供名称和补全，运行实例独立保存统计。Enum 完全可选，类装饰器提供配置；Python 声明编译为现有 schema，高频信号采集优先进入 C++ 覆盖引擎。

本草稿解决定义、引用和类型约束的问题。它不能仅凭一个合法的 Enum 或 dataclass 判断硬件是否真的发生过相应行为。

## 1. 草稿内容与当前能力的边界

| 文件 | 用途 | 是否包含运行时实现 |
| --- | --- | --- |
| [api.pyi](api.pyi) | 泛型、descriptor 和绑定对象的接口契约 | 否 |
| [usage.py](usage.py) | 完整声明示例、继承、嵌套字段及类型断言 | 否，仅供静态检查 |
| [native_usage.py](native_usage.py) | 无 Enum、装饰器、声明片段和 native 绑定草图 | 否，仅供静态检查 |
| [native-compilation.md](native-compilation.md) | C++ 降低、能力边界、回退、缓存及性能验收 | 否，含独立探针测量 |
| [native_probe.py](native_probe.py) | 最小类收集器，复用现有 Python/native 引擎验证和测量 | 是，独立受限探针 |
| [invalid_usage.py](invalid_usage.py) | 11 个应被类型检查器拒绝的错误用法 | 否，故意包含错误 |
| [pyrightconfig.json](pyrightconfig.json) | 只检查本目录草稿的独立配置 | 不适用 |

这里的 `CoverGroup[S]` 和 `CoverPoint[T]` 是候选声明 API，**不是当前 `xreactor.coverage` 中的同名实现**。未来若实现，建议先放在 `xreactor.declarative` 命名空间。草稿通过本地 `api` stub 表达，不添加任何实际包导出。

当前功能语义以[功能覆盖设计](../../features/functional-coverage.md)、[覆盖率指南](../../../docs/guides/coverage.md)和 [coverage.py](../../../src/xreactor/coverage.py) 为准。完整声明接口仍是候选行为；已执行的检查与最小探针能力见第 10 节及 native 编译草稿。

## 2. 为什么这样区分 bin、point、group

| 层次 | 用户写法 | 负责的内容 | 不负责的内容 |
| --- | --- | --- | --- |
| Bin | `busy = Bin.values(1)` | 匹配规则、normal/ignore/illegal/default、命中阈值 | 实例计数、字段取值、所属 group |
| Point | `class StatePoint(CoverPoint[int])` | 值类型、具名 bins、可复用的分桶定义 | 读取 DUT、推进时钟、推断事务归属 |
| Group | `class PipelineCoverage(CoverGroup[PipelineSample])` | point 槽位、字段绑定、Cross、采样契约 | 自动证明观测真实或检查通过 |
| 运行实例 | `PipelineCoverage(instance=...)` | 独立计数、transition 历史、provenance、报告 | 修改类上的 schema |

不要求为每个 bin 单独写子类：`class MainBin(...)` 会增加大量样板，并不能自然表达区间、mask 和 transition 等规则。Point 子类已经提供 bins 的命名空间，普通声明使用不可变工厂对象即可。自定义 matcher 是否开放仍受现有可序列化 schema 约束，不通过重写 Python `matches()` 绕过它。

类声明的主要目标是编写、补全、跳转、审核和模块组织，不要求值域使用 Enum。`int/bool` 可以直接定义 bins；确实需要具名取值时，Enum 是额外选择。bins 本身保持规则对象，Python Enum 的别名与成员继承限制不适合作为可扩展的分桶机制。[Python Enum 文档](https://docs.python.org/3/howto/enum.html)

PyVSC 已有类式 covergroup、point 属性和对象引用式 Cross，可作为表达方式的参考。本设计额外把字段类型和声明属性显式呈现给静态工具，避免主要接口依赖运行时动态添加属性。[PyVSC coverage 文档](https://pyvsc.readthedocs.io/en/stable/coverage.html)

类装饰器保留原类并配置描述、策略及 schema 身份，泛型基类提供公共方法和类型关系。装饰器不动态注入一套静态工具无法看到的 API，完整编译在装饰器应用完成后进行。用户也可以采用基类默认配置，装饰器不是每一层都必须写的样板。

## 3. 推荐的基本用法

```python
@dataclass(frozen=True)
class PipelineSample:
    state: int
    taken: bool

@coverpoint
class StatePoint(CoverPoint[int]):
    idle = Bin.values(0)
    busy = Bin.values(1, at_least=2)
    done = Bin.values(2)
    idle_to_done = Bin.transition(0, 1, 2)

@coverpoint
class TakenPoint(CoverPoint[bool]):
    yes = Bin.values(True)
    no = Bin.values(False)

@covergroup(
    schema_id="bpu.prediction",
    contract="bpu.observed-prediction/v1",
)
class PipelineCoverage(CoverGroup[PipelineSample]):
    state = StatePoint()
    taken = TakenPoint()
    state_taken = Cross(state, taken)

coverage = PipelineCoverage(instance="kunminghu_v3.bpu")
coverage.sample(observed)  # observed: PipelineSample，由观测流水线构造
count = coverage.state.count(StatePoint.busy)
```

示例是接口草图，完整 import 和 native 接线见 `native_usage.py`。带可选 Enum、嵌套字段及 LatencyPoint 的补充例子见 `usage.py`。`BinRule[...]` 属性注解可以省略，常见工厂仍能推导出类型；保留它可以让类型检查器直接检查每个 bin 声明。无论有没有注解，定义编译器都必须检查 bins 与 point 值域一致。

这里有两种不同访问：`PipelineCoverage.state` 返回声明引用，用于构造其他引用；`coverage.state` 返回 `BoundPoint[int]`，用于读取这个实例的计数。声明对象不保存任何运行计数。`StatePoint.busy` 是规则引用，不能直接调用它增加命中。首版不承诺 `coverage.state.busy` 这种动态成员访问；bins 通过 `StatePoint.` 补全，统计通过实例视图查询。

名称主要来自 Python 标识符。保留的字符串用于 schema 身份、采样契约、实例路径及外部报告标签；不会要求调用者再次手写 `"source"`、`"main"` 来完成内部引用。

## 4. 字段绑定：简洁默认值与显式补全

### 4.1 默认同名绑定

`state = StatePoint()` 默认绑定 `PipelineSample.state`。第一次 `compile()` 或实例化时，编译器解析 `CoverGroup[PipelineSample]`，检查样本类型、字段存在性和字段类型，不等到第一次采样才发现问题。

这只是命名约定。普通 Python 类型检查器不会从两个类的同名属性自动推导它们之间的绑定关系；重命名样本字段后，也不能保证 IDE 同时改写 group 属性。需要更强的字段补全和重构支持时，使用显式路径。

### 4.2 无字段字符串的显式路径

```python
fields = Fields(Envelope)

class EnvelopeCoverage(CoverGroup[Envelope]):
    source = SourcePoint(
        source=fields.select(lambda s: s.prediction.source)
    )
    result = SourcePoint(
        source=fields.select(lambda s: s.output_source)
    )
```

`Fields(Envelope)` 让 lambda 参数具有 `Envelope` 类型，因此 `s.prediction.` 有字段补全，返回值推导为 `Source`。把整数路径传给 `SourcePoint` 会形成 `FieldRef[int]` 与 `FieldRef[Source]` 的类型冲突。

`select()` 是**定义期字段路径构造器**。候选实现向 selector 传入符号根节点，属性访问逐段记录 dataclass 字段，最后必须返回同一根节点派生的路径。编译后保存 `("prediction", "source")`；不保存 lambda，不在每次采样时执行它，也不依赖 `inspect.getsource()` 或 lambda 源码位置。

首版只支持普通 frozen dataclass 及其嵌套字段；不支持 property、下标、方法调用、运算、按样本分支或任意 Python extractor。比如 `lambda s: s.latency + 1` 在静态上可能合法，但必须被路径构造器拒绝。派生值由 Monitor/collector 先计算成样本字段。符号代理只验证路径结果，不是执行 Python 定义代码的安全沙箱。

`FieldRef[T]` 的泛型只携带叶值类型，运行时还保留根样本类型。把另一个 dataclass 的同类型字段绑定进当前 group，静态工具未必能发现，编译器必须拒绝。

### 4.3 Gate 使用同一套字段引用

```python
fields = Fields(PredictionSample)

class CheckedPredictionCoverage(
    PredictionCoverage,
    contract="bpu.checked-prediction/v1",
    iff=Iff.truth(fields.select(lambda s: s.checked)),
):
    pass
```

`Iff.equals/not_equals/truth` 编译为现有字段比较，group、point、cross 都可以使用。首版不开放任意 predicate。字段路径和根类型接受与 point 相同的检查；gate 必须在修改计数或历史之前完成求值。派生 group 省略 `iff` 时继承已有 gate，显式 `iff=None` 时清除 gate；实现使用内部 sentinel 区分两种情况，stub 用省略默认值表达该约定。

## 5. 引用、继承与重新绑定

### 5.1 Point 继承

```python
class StrictLatencyPoint(LatencyPoint):
    long: BinRule[int] = Bin.range(4, 7)
    timeout: BinRule[int] = Bin.illegal(Bin.range(8, 15))
```

不同名 bins 合并，同名 bin 显式覆盖，未覆盖的 `short` 保留。最终 bins 重新进行类型、重叠、排除和默认桶检查。首版不设计通过 `None` 隐式删除 bin 的语法；需要完全不同的分桶时定义新的 Point 子类。

Bin 对象按声明身份引用，不能使用 matcher 的结构相等性代替身份。两个独立声明即使匹配规则相同，也不能被自动合并。编译器保存 `(声明类, 属性名, 规则对象)`，不把可变的 owner/name 写回不可变规则。

同一继承链上的 bin 属性名表示同一逻辑位置。例如在 Strict point 上查询 `LatencyPoint.long`，解析到最终的 `long`，即 4–7；不会查询已经被替换的旧规则计数。`StrictLatencyPoint.long` 更清楚，示例优先使用它。与当前 point 无继承关系的同类型 bin 引用必须报错。

同一个规则对象不能在 Point 定义中作为不同的 bin 属性重复声明，也不能在互不相关的 Point 类中重新登记。复用同一个 Point 类到多个 group 槽位是允许的。同一个 point 声明实例不能赋给两个属性或重新挂到无关的 group 类；需要复用定义时分别构造 `SourcePoint()`，每个对象对应独立槽位。正常继承或显式 CoverageFragment 组合不视为重复赋值，最终实例仍各自保存状态。

### 5.2 Group 继承与 Cross

```python
class StrictPredictionCoverage(PredictionCoverage):
    latency = StrictLatencyPoint()
    # source_latency 自动引用最终的 source / latency 槽位。
```

Cross 接收到的 point 对象在编译时转换为 group 内的逻辑槽位。槽位身份包含声明 group 与属性名，类上访问 descriptor 时保留访问的 group 上下文；编译器建立“祖先槽位 → 最终槽位”的映射，派生类可以重新绑定祖先引用，无关 group 的同名引用不在映射中。继承合并完成后再解析 Cross，不能把基类中旧的 `latency` 对象直接保留为运行时绑定。只覆盖 point 时，继承的 Cross 使用新 bins 重新展开；同名 Cross 的显式声明则替换旧 Cross。

覆盖整个 point 声明会替换该槽位的 source、iff、weight 等构造配置，不自动复制旧对象的参数。例如嵌套字段绑定需要在替换 point 时重新明确；省略后按最终槽位同名绑定，无法找到字段就编译失败。Point 类继承负责 bins 的复用，group 槽位覆盖负责绑定配置，两者不能混为一件事。

同名 point 覆盖首版要求值类型相同、声明类为原 Point 类的子类，这也有利于保留静态成员类型。如果要改变 point 的值域或换成不相关的定义，应新建 group，避免基类方法与已有引用失去含义。

带 bin 选择的 Cross 同样按最终槽位和逻辑 bin 名重新解析。覆盖后若某个 bin 不再是 Cross 可用的 normal bin，编译必须失败，不能静默删除该组合。基类中所有对象和已创建的基类实例保持原定义。

候选范围保留一条具体 Group 继承链，并允许多个显式 `CoverageFragment[S]` 片段组合；不任意合并两个具体 Group 的运行行为。片段样本类型必须一致，独立来源的重名必须显式处理，共同祖先的同一声明只收集一次，不能以 Python MRO 静默选择覆盖计划。补全示例已在 `native_usage.py` 静态验证，完整冲突编译尚未实现。`CoverGroup` 的样本类型继承后保持不变，换成另一个样本类型时新建 group。

覆盖 bin 时不隐式重算基类类体。基类 `long = Bin.range(4, LIMIT)` 已经求值，子类只覆盖 LIMIT 不会改变 long；应显式覆盖 bin，后续参数化方案必须把参数纳入 schema。独立的多个 group 可以用普通 Python 对象聚合并按模块组织，不自动支持跨 group Cross。

### 5.3 相同值域的多个 point

```python
source = SourcePoint(source=fields.select(lambda s: s.prediction.source))
result = SourcePoint(source=fields.select(lambda s: s.output_source))
source_result = Cross(source, result)
```

两者共享 `SourcePoint` 类和规则对象，但运行统计完全独立。显式 Cross 组合必须引用槽位：

```python
source_latency = Cross(
    source,
    latency,
    ignore=((source.bin(SourcePoint.ahead), latency.bin(LatencyPoint.long)),),
)
```

`BinSelection` 同时携带 point 槽位和 bin 引用，不接受裸 `SourcePoint.main` 作为 Cross 组合的一维。组合长度、维度顺序、所属 group 和 normal-bin 资格都在编译时检查。查询 `BoundCross.count(...)` 时执行相同归属检查，不能引用另一个 group 的槽位。

## 6. 编译与运行时边界

类定义阶段只收集不可变声明。第一次 `compile()` 解析类型注解、字段路径和继承，生成不可变 `CompiledGroup[S]`；失败时不创建运行实例。随后相同类复用编译结果，派生类单独编译，不修改基类缓存。编译后的声明不可再 monkey patch；发现声明变更应拒绝使用，不能继续返回旧缓存。

建议的编译顺序是：

1. 解析唯一的具体样本 dataclass 和 point 值域，拒绝 `Any`、未绑定泛型及首版不支持的联合/容器类型。
2. 合并 point/bin/cross 声明，检查保留名称、重复对象及覆盖合法性。
3. 检查同名绑定或显式路径，以及所有 gate 的类型和根样本。
4. 将所有引用变成最终 point/bin 位置，展开 Cross，执行已有 schema 验证。
5. 生成 `CoverGroupDef / CoverPointDef / CrossDef`、字段提取计划和输入类型契约，冻结缓存。

手动 transaction 的采样路径是：

```text
Monitor / collector 生成同一时刻的不可变样本
  -> 检查样本及受支持字段的运行时类型
  -> 按预编译路径提取 point/gate 字段，构造规范化快照
  -> 已有引擎规划匹配结果
  -> 已有引擎一次性提交计数、历史与 provenance
```

运行时必须补齐 Python 注解不强制执行的检查。Enum 先确认所属类，再取 `.value`；不能把其他 Enum、裸整数或字符串自动转换成目标 Enum。整数域需要排除 `bool`，即使 Python 静态类型系统把 bool 当作 int 子类型。首版叶值限于已有引擎支持的标量及底层为受支持标量的普通 Enum，枚举域推荐 `@unique`。

手动 sample 的类型检查覆盖样本契约中的所有字段，发生在 gate 之前；不把错误类型当作 gated-off 样本。native 在绑定时检查实际使用字段的物理表示，不逐拍构造 Python dataclass。强类型 native 首轮使用 int/bool；Enum 信号解码及非法编码语义另行设计。这些是候选前端约束，不能反推当前低层 API 已经采用同样约束。

字段缺失、类型错误、引用错误或提取异常不得改变计数和 transition 历史。匹配到 illegal bin 则继续沿用现有策略：提交相应统计后按策略报错或记录；它与提交前的无效输入不同。

类上的声明对象不保存计数或 transition 状态。每个 group 实例新建现有运行引擎及其状态；`BoundPoint / BoundCross` 仅保存所属实例和逻辑位置。descriptor 的 `__set__` 拒绝用户给运行实例的 point/cross 重新赋值。`sample`、`compile`、`snapshot` 等 API 名称和私有属性名不能作为声明名称。

## 7. Schema 身份、合并与现有引擎复用

现有 schema 会把 Enum 转成标量，仅靠其 digest 无法区分两个底层编码相同的 Enum 值域。这里必须保留两项身份，不能宣称新类型信息已自动进入旧 digest：

| 身份 | 内容 | 作用 |
| --- | --- | --- |
| `schema_digest` | 现有 `CoverGroupDef` 的规范化 JSON | 检查 bins、阈值、Cross、gate 等计数语义 |
| `input_digest` | 前端版本、样本/Enum 类型标识、枚举成员及编码、字段类型、实际绑定路径 | 检查观测输入和归一化语义 |

首版可采用 `module + qualname` 标识样本和 Enum 类型，包含嵌套字段类型；不包含对象地址、selector 代码对象或 Python hash。稳定性跨进程验证，类型重命名导致不再可合并是可接受的保守行为。显式稳定类型 ID 可以以后单独设计。

`contract` 说明观测语义，例如 `bpu.observed-prediction/v1`。候选前端把它与前端版本、`input_digest` 组合为确定的字符串，传给现有 `CoverGroupDef.instantiate(contract=...)`。当前引擎已经检查 `sampling_contract` 相等，因此可复用该检查拒绝输入契约不同的报告，而不把类型信息塞进无约束的 run metadata。未显式声明 contract 时，用 schema_id 派生默认身份，但它不包含项目观察器算法的语义；正式模型应显式声明并在算法含义改变时更新版本。

`schema_id` 默认使用 group 的模块及限定类名；显式 ID 是外部身份边界，可以保留字符串。继承时显式 ID/contract 继承，默认 ID 按最终类推导；bins、字段映射和类型契约仍单独参与校验，不能靠相同 ID 绕过合并检查。

输入契约 JSON 必须可单独导出供审阅。首版不修改既有 report 格式，报告里的 contract 保存其指纹，原始契约与模型定义一起保存；若以后需要在单份报告中展开契约，应显式设计报告版本。不同声明顺序只要最终定义与绑定相同，两个 digest 就应相同。

编译器把 point 和 gate 映射到规范化快照的确定性字段键，使当前引擎无需理解 Python 类、descriptor 或 selector。现有 normal/ignore/illegal/default 优先级、at_least、overlap、同样本 Cross、分母、transition、4096 自动组合上限以及报告合并规则继续使用原实现。

候选前端同时面向手动 transaction 和 native 信号采集。字段 selector 在定义阶段生成路径，wire 显式绑定物理信号；它不新增时钟、task 或 Execution 生命周期。native lowering、绑定契约和能力选择见 [C++ 执行草稿](native-compilation.md)，首轮复用现有 ABI v3。

## 8. 静态工具究竟能检查什么

| 错误或能力 | 静态工具 | 定义编译 / 运行时 |
| --- | --- | --- |
| `sample(dict)` 代替 `PredictionSample` | 能拒绝 | 仍检查真实对象类型 |
| 把字符串或另一类 Enum 放进 `source` | 能拒绝正常带类型的调用 | 必须拒绝绕过注解的对象 |
| 显式路径字段拼错、叶类型错误 | 能拒绝 | 必须复验 |
| 带 `BinRule[Source]` 注解的 bin 值域错误 | 能拒绝 | 无注解时也必须检查 |
| `count()` 传入另一种值类型的 bin | 能拒绝 | 仍检查归属 |
| 相同 `int` 值域但来自无关 Point 类的 bin | 不保证 | 必须拒绝 |
| 同名绑定缺字段、类型不匹配 | 不保证 | 编译失败 |
| 显式路径来自另一种根样本 | 不保证 | 编译失败 |
| Cross 维度归属、重复槽位、非法组合 | 不保证 | 编译失败 |
| 派生 group 的 Cross 重新绑定 | 只能验证成员类型 | 必须实现并验证绑定语义 |
| 观测是否来自 DUT、checked 是否真的检查通过 | 不能 | 由项目观测/检查流水线负责 |

补全依赖显式类属性、标准泛型和有类型的 descriptor，不通过 `__getattr__ -> Any` 模拟。`usage.py` 的 `assert_type` 验证类型结果；实际编辑器的候选列表和重命名体验仍需实测。

未来发布时还要正确打包类型信息；不能只在源码树里“看起来有注解”。内联类型包或相应 stub 的发行方式需遵循 Python 类型信息分发规范，本轮不添加 `py.typed` 或修改打包配置。[Python typing 分发规范](https://typing.python.org/en/latest/spec/distributing.html)

## 9. 与硬件验证语义的关系

把 `"main_return_full"` 换成 `Phase.MAIN_RETURN_FULL` 只消除了拼写风险，不能使测试脚本中的一次调用自动成为硬件行为证据。对于 BPU 这类模型，更推荐 observer 从实际 transaction 派生 `source`、方向、目标地址关系等字段，再由 points 和 Cross 描述覆盖空间。

并非所有行为都适合简单 Cross。需要比较两次预测、跨周期关联或判定一次完整 repair 时，应由项目 collector 输出明确的派生结果；本接口不把一串 testcase 检查点自动当作事务时序。发生过的行为和检查通过的行为可以分别统计，例如 `PredictionCoverage` 与带 `checked` gate 的 group，但 checked 的来源仍由检查流水线保证。

类式声明也可以表示字符串值域或人工分类字段，通用引擎不必全面禁止它们。推荐 API 使用结构化样本与明确的 point/bin 引用；业务值域是否采用 Enum 由项目决定，是否作为硬件功能覆盖证据取决于绑定到什么观测和采样契约。[现有观测覆盖讨论](../../verification/coverage-without-test-tags-2026-09.md)

UCIS 关注覆盖数据模型和互操作，不指定这种 Python 声明语法。本草稿保留 group/point/bin/cross 等概念，但没有实现 UCIS 导出，也不据此声明 UCIS 合规；相关标准分析沿用[已有审查](../../verification/coverage-standards-review-2026-09.md)。

## 10. 本轮验证与未来实现验收

已使用 **Pyright 1.1.414 / Python 3.11 类型目标**检查独立接口草稿：正例、native 接线示例和 stub 严格模式为 0 errors、0 warnings；负例的 11 个 `EXPECT_ERROR` 位置全部被拒绝，共 17 条诊断，多条来自拼写错误引发的 Unknown 类型传播。接口检查没有使用错误抑制或 `Any` 放宽。配置关闭的 `reportMissingModuleSource` 只对应故意没有运行时模块的 `.pyi`，并非关闭类型错误。

在有 Pyright 的环境中，从仓库根目录复现：

```bash
pyright -p design/drafts/class-coverage/pyrightconfig.json
pyright -p design/drafts/class-coverage/pyrightconfig.json design/drafts/class-coverage/invalid_usage.py
```

第一条应成功，第二条应失败并指向 11 个标注位置。`usage.py`、`native_usage.py`、`invalid_usage.py` 不应作为 Python 程序执行；可运行探针是 `native_probe.py`，它复用现有计数引擎，不加入项目 pytest 入口。独立探针已完成统计差分与微基准，范围见 native 草稿；以下仍是完整前端实现必须兑现的验收项：

1. 两个相同 group 实例的计数、transition 历史和 provenance 完全隔离。
2. 基类实例创建前后再编译派生类，基类 schema/计数都不改变。
3. 覆盖 `latency` 后，继承 Cross 的组合和命中都使用新 bins；无效选择在采样前拒绝。
4. 同一 Point 类的两个槽位独立计数，跨 group 及无关 Point 的同类型引用被拒绝。
5. 错误 Enum、bool-as-int、缺字段或提取失败均不产生部分计数/历史；illegal 命中符合现有策略。
6. 定义期 selector 编译为字段路径，采样时不执行用户 lambda；不支持的表达式在定义编译前后明确报错。
7. 同语义声明生成相同 schema 和输入契约；不同 Enum 域、绑定或 gate 导致相应身份变化，合并被拒绝。
8. 与手写现有 schema 对照，normal/ignore/illegal/default、at_least、Cross、transition 和报告结果一致；采样开销单独测量。
9. 在编辑器中检查字段、bin、point 的补全和重命名，并验证构建出的发行包仍可获得这些类型信息。

优先实现声明收集、装饰器、单继承、字段路径及 Cross，再接入 native 能力预检与整组计数；片段组合、完整差分和编辑器体验随后补齐。`api.pyi` 中 `Bin.default(type)` 的显式类型参数用于确定叶值域；它与现有低层工厂签名不同，未来是否统一单独确定。bin arrays、任意 extractor、任意多继承和复杂关联 DSL 尚不冻结。
