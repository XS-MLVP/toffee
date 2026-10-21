# Coverage v2 草稿：程序回收与两态表达式

2026-10-09，在 picker `60ca8e8` / xcomm `83ab5b2` 适配基线上继续修复。
配套原生提交见 `.github/native-dependencies.json`。

已实现长 Execution 内程序回收、失败编译清理，以及位宽、signed、cast 和宽数据运算。
沿用现有 XExpr/Sequence/FSM、ExprEngine、PatternMatcher 与 coverage 计数，
没有新增引脚容器、时序语言或 Program 类。X/Z 处理暂缓。
集成以新版协议 5 为基础补充回收和类型运算，原生 ABI 为 3。本文下方历史资源测量来自较早草稿；集成分支重新运行相同审查脚本与行为回归。

## 回收解决了什么

修复前，coverage 常驻时完成 2,000 个不同参数等待，仍保留 2,000 份缓存和额外
6,000 个 C++ 节点；仅一个 registration 存活。整个 Execution 结束后才清空。

| 同一实验的指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 缓存份数 | 2,000 | 256 |
| C++ 节点 | 新增 6,000，持续增长 | 峰值 1,671；主动回收后共 837 |
| 相同热点程序重复 64 次 | 新增 0 节点 | 新增 0 节点 |
| 部分编译失败 64 次 | 残留 256 节点 | 新增残留 0 |
| 容量不足、arm 失败 64 次 | 残留 192 节点、64 份缓存 | 新增残留和缓存均为 0 |
| 常驻 coverage FSM | 正常完成 | 跨回收后仍正常完成 |
| Execution 结束后 | 清空 | 清空 |

837 是这个实验的活动条件加缓存，不是所有项目通用的节点上限。
回归另执行 3,000 次等待，逐次检查资源有界。

```python
backend = XCommClockBackend(clock, program_cache_capacity=256)
backend.collect_programs()  # 在安全边界主动回收
```

缓存使用 LRU，默认 256 份，设为 0 可关闭。缓存保留程序绑定的 Python 对象，
避免借用的 XData 指针悬空。自动回收阈值至少 256，并按回收后节点数的两倍调整，
允许少量垃圾暂留，不逐拍扫描 DAG。

## 怎样复用现有引擎

现有 ExprEngine 增加 DAG 标记、压缩和宽常量/mask 释放；压缩返回 old-to-new 映射。
保留并更新所有 occupied watcher，包括已命中、尚待 rearm 的 watcher，以及其
表达式/Sequence/FSM；也保留 coverage 的 source、group/item gates、abort 和所有 bin。
普通 bin 不一定进入 Python 缓存，不能仅扫描缓存。

缓存中的程序也作为根。活动程序不因缓存淘汰而失效，原生 watcher/coverage 仍保留其根。
压缩只更新引用，不重建 matcher，FSM 状态、Sequence 年龄、overlap attempts 和计数保持。
未使用的宽常量和 mask 随不可达节点释放；DUT 自己的 XData 是借用信号，不由回收释放。

降低与 Attach 位于同一个编译安全区，禁止回收尚未挂入 engine 的临时根。
失败后撤销本次新缓存、释放临时 registration，再回收不可达节点。
部分降低失败、容量不足、Attach 失败均有回归。

```cpp
auto mapping = engine.CollectExpressions(retained_roots);
root = mapping[root];
auto nodes = engine.ExpressionNodeCount();
auto constants = engine.ExpressionConstantCount();
```

这是显式安全点 API。engine 自动更新自己的 watcher/coverage 引用；直接 C++ 调用者
必须提交自己的 retained roots，并更新所有存储的 root。压缩后旧下标失效。
C++ Disarm 本身不隐式移动外部 root；Python backend 在自己的安全边界调用回收。

## 两态类型和写法

既有 IR 增加 BitsType(width, signed)、BoolType、显式常量和 cast 节点。
Bundle/XData 提供物理位宽，signedness 显式声明，无需 enum 或新的信号包装系统。

```python
from xreactor import bits
from xreactor.ir import signal_expr

data = signal_expr(pins.data)
negative = data.as_signed() < 0
wrapped = (data + 1) == 0
extended = data.as_signed().resize(129)
low_byte = data.bit_and(255)
overflow = (bits(1, 64) << 64) == 0
```

这些表达式直接用于已有 @xtrigger、Wait/Within、FSM transition 和 Bin.pattern。
类、继承、装饰器和 typed Bundle 的组织方式不变。

| 操作 | 规则 |
| --- | --- |
| `bits(value, width, signed=...)` | 显式常量必须可表示，越界报错 |
| `as_signed()` / `as_unsigned()` | 同位宽重新解释符号；8 位 0xff 解释为 signed 是 -1 |
| `resize(width)` | 按源 signedness 符号/零扩展，缩小时截断 |
| `resize(width, signed=...)` | 先按源类型扩展/截断，再按目标 signedness 解释 |
| `+ - *`、一元负号 | 使用操作数位宽，结果截断；8 位 255+1 为 0 |
| `//`、`%` | signed 除法向零截断，余数随被除数符号；除零报错 |
| `<<`、`>>` | 达到/超过位宽不回绕；signed 右移补符号位；负移位量报错 |
| `bit_and/or/xor/not` | 数据位运算，保留位宽和 signedness |
| 逻辑 `&`、`\|`、`~` | 保持逻辑组合含义，返回 Bool |
| 比较 | typed 数据要求同位宽、同 signedness，返回 Bool |

普通整数常量按 typed 操作数类型检查。负数需要 signed 上下文，如 `data.as_signed() == -1`；
裸 unsigned 信号比较负数仍在原生降低时报错。不同类型需要显式转换。
`a.as_signed().resize(16)` 符号扩展，`a.as_signed().as_unsigned().resize(16)` 零扩展。

新数据运算进入 typed DAG；已有直接无符号比较和 C++ untyped scalar API 保留原行为。
旧 scalar SHL 仍按 64 位标量运算，新 typed SHL 才遵循上述位向量规则。
宽运算在同一 ExprEngine 中使用 XData 字节存储，没有外部大整数依赖。
类型、cast、literal 类别和物理位宽进入共享/缓存 key，新增节点支持既有 IR 序列化。
Python 参考执行采用同样的两态规则；这不等同于完整 SystemVerilog 表达式语言。

## 编译和验证

```text
class / @xtrigger 声明
    → 现有 XExpr / SequenceSpec / FsmSpec
    → 绑定 Bundle/XData，推导并检查类型
    → 构造现有 ExprEngine 内的 typed DAG
    → 现有 PatternMatcher 推进，terminal 分发到 bin 计数
```

严格原生执行显式选择 `strategy="native"`，配套能力缺失时明确报错。
不能通过 Python fallback 宣称所有 bin 已 compiled。
差分回归覆盖 1/8/9/32/64/65/128 位 signed/unsigned 运算、cast、比较、移位、越界常量、
混合类型拒绝、除零和序列化，也覆盖 129 位结果及 picker 的 W()==0 标量引脚。
共享 FSM/Sequence bin 在回收后仍由 C++ 推进。

真实 picker Adder 示例增加 129 位加法关系和 128 位 signed 判断：
9 个 bin 使用 5 份原生执行和一个采样注册，算术正确 bin 命中 12 次，signed-negative 命中 2 次。

```bash
python -m experiments.coverage_v2_engine_audit --output /tmp/coverage-v2-engine-audit.json
python -m pytest -q tests/integration/test_program_collection.py tests/integration/test_typed_expressions.py --require-xspcomm
```

资源数量来自原生查询，不是 RSS。压缩会移动 root，不能用下标测量节点数量。

## 保留的边界

X/Z 传播和有效性刷新暂缓，已有处理保持原状。字段捕获、动态 key 关联、沿途 emit 和
时序 cross 的完成上下文仍单独设计；本轮不承诺完整 UCIS 兼容或统一诊断框架。
