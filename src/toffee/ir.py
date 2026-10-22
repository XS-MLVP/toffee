from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class SupportsEval(Protocol):
    def evaluate(self, dut: Any) -> Any: ...


class XExpr:
    def as_signed(self) -> "XExpr":
        return CastExpr(self, None, True)

    def as_unsigned(self) -> "XExpr":
        return CastExpr(self, None, False)

    def resize(self, width: int, *, signed: bool | None = None) -> "XExpr":
        return CastExpr(self, width, signed)

    def bit_and(self, other: Any) -> "XExpr":
        return BinaryExpr("bit_and", self, as_expr(other))

    def bit_or(self, other: Any) -> "XExpr":
        return BinaryExpr("bit_or", self, as_expr(other))

    def bit_xor(self, other: Any) -> "XExpr":
        return BinaryExpr("bit_xor", self, as_expr(other))

    def bit_not(self) -> "XExpr":
        return UnaryExpr("bit_not", self)

    def __neg__(self) -> "XExpr":
        return UnaryExpr("neg", self)

    def __add__(self, other: Any) -> "XExpr":
        return BinaryExpr("add", self, as_expr(other))

    def __radd__(self, other: Any) -> "XExpr":
        return BinaryExpr("add", as_expr(other), self)

    def __sub__(self, other: Any) -> "XExpr":
        return BinaryExpr("sub", self, as_expr(other))

    def __rsub__(self, other: Any) -> "XExpr":
        return BinaryExpr("sub", as_expr(other), self)

    def __mul__(self, other: Any) -> "XExpr":
        return BinaryExpr("mul", self, as_expr(other))

    def __rmul__(self, other: Any) -> "XExpr":
        return BinaryExpr("mul", as_expr(other), self)

    def __floordiv__(self, other: Any) -> "XExpr":
        """Fixed-width integer division truncates toward zero, including signed operands."""
        return BinaryExpr("div", self, as_expr(other))

    def __mod__(self, other: Any) -> "XExpr":
        return BinaryExpr("mod", self, as_expr(other))

    def __rfloordiv__(self, other: Any) -> "XExpr":
        return BinaryExpr("div", as_expr(other), self)

    def __rmod__(self, other: Any) -> "XExpr":
        return BinaryExpr("mod", as_expr(other), self)

    def __lshift__(self, other: Any) -> "XExpr":
        return BinaryExpr("shl", self, as_expr(other))

    def __rshift__(self, other: Any) -> "XExpr":
        return BinaryExpr("shr", self, as_expr(other))

    def __rlshift__(self, other: Any) -> "XExpr":
        return BinaryExpr("shl", as_expr(other), self)

    def __rrshift__(self, other: Any) -> "XExpr":
        return BinaryExpr("shr", as_expr(other), self)

    def evaluate(self, dut: Any) -> Any:
        raise NotImplementedError

    def __bool__(self) -> bool:
        raise TypeError(
            "XExpr cannot be converted to bool; use '&', '|' and '~' "
            "instead of Python 'and', 'or' and 'not'"
        )

    def __and__(self, other: Any) -> "XExpr":
        return BinaryExpr("and", self, as_expr(other))

    def __rand__(self, other: Any) -> "XExpr":
        return BinaryExpr("and", as_expr(other), self)

    def __or__(self, other: Any) -> "XExpr":
        return BinaryExpr("or", self, as_expr(other))

    def __ror__(self, other: Any) -> "XExpr":
        return BinaryExpr("or", as_expr(other), self)

    def __invert__(self) -> "XExpr":
        return UnaryExpr("not", self)

    def __eq__(self, other: Any) -> "XExpr":  # type: ignore[override]
        return BinaryExpr("eq", self, as_expr(other))

    def __ne__(self, other: Any) -> "XExpr":  # type: ignore[override]
        return BinaryExpr("ne", self, as_expr(other))

    def __lt__(self, other: Any) -> "XExpr":
        return BinaryExpr("lt", self, as_expr(other))

    def __le__(self, other: Any) -> "XExpr":
        return BinaryExpr("le", self, as_expr(other))

    def __gt__(self, other: Any) -> "XExpr":
        return BinaryExpr("gt", self, as_expr(other))

    def __ge__(self, other: Any) -> "XExpr":
        return BinaryExpr("ge", self, as_expr(other))


@dataclass(frozen=True, slots=True, eq=False)
class PythonPredicateExpr(XExpr):
    """Serializable predicate identity; executable Python stays process-local."""
    identity: str
    arguments: tuple = ()
    keywords: tuple = ()
    _predicate: Callable[[Any], bool] | None = field(default=None, repr=False, compare=False)

    def evaluate(self, dut: Any) -> bool:
        if self._predicate is None:
            raise RuntimeError("Python predicate from a report must be rebound to its declaration")
        return self._predicate(dut)


@dataclass(frozen=True, slots=True, eq=False)
class ConstantExpr(XExpr):
    value: Any

    def evaluate(self, dut: Any) -> Any:
        return self.value


@dataclass(frozen=True, slots=True, eq=False)
class SignalExpr(XExpr):
    path: tuple[str, ...]

    def __getattr__(self, name: str) -> "SignalExpr":
        return SignalExpr(self.path + (name,))

    def evaluate(self, dut: Any) -> Any:
        value = resolve_path(dut, self.path)
        return _read_bound_signal(value)


@dataclass(frozen=True, slots=True, eq=False)
class BoundSignalExpr(XExpr):
    signal: Any

    def evaluate(self, dut: Any) -> Any:
        del dut
        return _read_bound_signal(self.signal)


@dataclass(frozen=True, slots=True, eq=False)
class UnaryExpr(XExpr):
    op: str
    operand: XExpr

    def evaluate(self, dut: Any) -> Any:
        if _uses_typed(self):
            return _evaluate_typed(self, dut)
        value = self.operand.evaluate(dut)
        if self.op == "not":
            return not bool(value)
        raise ValueError(f"unsupported unary operation: {self.op}")


@dataclass(frozen=True, slots=True, eq=False)
class BinaryExpr(XExpr):
    op: str
    left: XExpr
    right: XExpr

    def evaluate(self, dut: Any) -> Any:
        if _uses_typed(self):
            return _evaluate_typed(self, dut)
        left = self.left.evaluate(dut)
        if self.op == "and":
            return bool(left) and bool(self.right.evaluate(dut))
        if self.op == "or":
            return bool(left) or bool(self.right.evaluate(dut))
        right = self.right.evaluate(dut)
        operations = {
            "eq": lambda: left == right,
            "ne": lambda: left != right,
            "lt": lambda: left < right,
            "le": lambda: left <= right,
            "gt": lambda: left > right,
            "ge": lambda: left >= right,
        }
        try:
            return operations[self.op]()
        except KeyError as exc:
            raise ValueError(f"unsupported binary operation: {self.op}") from exc


@dataclass(frozen=True, slots=True)
class BitsType:
    width: int
    signed: bool = False

    def __post_init__(self):
        if type(self.width) is not int or not 1 <= self.width <= (1 << 32) - 1:
            raise ValueError("bit width must be a positive uint32 integer")
        if type(self.signed) is not bool:
            raise TypeError("signed must be bool")


@dataclass(frozen=True, slots=True)
class BoolType:
    pass


ValueType = BitsType | BoolType
BOOL = BoolType()


@dataclass(frozen=True, slots=True, eq=False)
class BitsConstExpr(XExpr):
    value: int
    width: int
    signed: bool = False

    def __post_init__(self):
        _check_literal(self.value, BitsType(self.width, self.signed))

    def evaluate(self, dut: Any) -> int:
        return self.value


@dataclass(frozen=True, slots=True, eq=False)
class CastExpr(XExpr):
    operand: XExpr
    width: int | None
    signed: bool | None

    def __post_init__(self):
        if self.width is not None:
            BitsType(self.width)
        if self.signed is not None and type(self.signed) is not bool:
            raise TypeError("signed must be bool or None")

    def evaluate(self, dut: Any) -> int:
        return _evaluate_typed(self, dut)


def bits(value: int | XExpr, width: int, *, signed: bool = False) -> XExpr:
    """Construct a checked literal or explicitly cast an existing expression."""
    if isinstance(value, XExpr):
        return CastExpr(value, width, signed)
    return BitsConstExpr(value, width, signed)


def _check_literal(value: Any, kind: BitsType) -> None:
    if type(value) is not int:
        raise TypeError("Bits literal must be int; cast Bool explicitly")
    low = -(1 << (kind.width - 1)) if kind.signed else 0
    high = (1 << (kind.width - int(kind.signed))) - 1
    if not low <= value <= high:
        raise ValueError(f"constant {value} does not fit {kind}")


def _uses_typed(expression: XExpr) -> bool:
    if isinstance(expression, (BitsConstExpr, CastExpr)):
        return True
    if isinstance(expression, UnaryExpr):
        return expression.op in ("bit_not", "neg") or _uses_typed(expression.operand)
    if isinstance(expression, BinaryExpr):
        return expression.op in ("add", "sub", "mul", "div", "mod", "bit_and", "bit_or", "bit_xor", "shl", "shr") or _uses_typed(expression.left) or _uses_typed(expression.right)
    return False


def _operand_types(expression: BinaryExpr, dut: Any) -> tuple[ValueType, ValueType]:
    left, right = expression.left, expression.right
    if isinstance(left, ConstantExpr) and isinstance(right, ConstantExpr) and type(left.value) is int and type(right.value) is int and expression.op not in ("and", "or", "shl", "shr"):
        common = BitsType(max(1, left.value.bit_length(), right.value.bit_length()))
        a, b = expression_type(left, dut, common), expression_type(right, dut, common)
    elif isinstance(left, ConstantExpr) and type(left.value) is int and not isinstance(right, ConstantExpr):
        b = expression_type(right, dut)
        a = expression_type(left, dut, b if isinstance(b, BitsType) else None)
    else:
        a = expression_type(left, dut)
        b = expression_type(right, dut, a if isinstance(a, BitsType) and expression.op not in ("shl", "shr", "and", "or") else None)
    if expression.op in ("and", "or", "shl", "shr"):
        if expression.op in ("shl", "shr") and (not isinstance(a, BitsType) or not isinstance(b, BitsType)):
            raise TypeError("shift operands must be Bits")
        return a, b
    if a != b:
        raise TypeError(f"expression operands require the same type, got {a} and {b}; use resize/as_signed/as_unsigned")
    if isinstance(a, BoolType) and expression.op not in ("eq", "ne"):
        raise TypeError("data operation requires Bits, not Bool")
    return a, b


def expression_type(expression: XExpr, dut: Any, context: BitsType | None = None) -> ValueType:
    from .signals import signal_width
    if isinstance(expression, BitsConstExpr):
        return BitsType(expression.width, expression.signed)
    if isinstance(expression, ConstantExpr):
        if type(expression.value) is bool:
            return BOOL
        if type(expression.value) is not int:
            raise TypeError("typed expressions require bool/int constants")
        kind = context or BitsType(max(1, expression.value.bit_length()))
        _check_literal(expression.value, kind)
        return kind
    if isinstance(expression, (SignalExpr, BoundSignalExpr)):
        signal = resolve_path(dut, expression.path) if isinstance(expression, SignalExpr) else expression.signal
        return BitsType(signal_width(signal))
    if isinstance(expression, CastExpr):
        original = expression_type(expression.operand, dut)
        width, signed = (original.width, original.signed) if isinstance(original, BitsType) else (1, False)
        return BitsType(expression.width if expression.width is not None else width,
                        expression.signed if expression.signed is not None else signed)
    if isinstance(expression, UnaryExpr):
        operand = expression_type(expression.operand, dut)
        if expression.op == "not":
            return BOOL
        if not isinstance(operand, BitsType) or expression.op not in ("bit_not", "neg"):
            raise TypeError("unary data operation requires Bits")
        return operand
    if isinstance(expression, BinaryExpr):
        a, _ = _operand_types(expression, dut)
        if expression.op in ("and", "or", "eq", "ne", "lt", "le", "gt", "ge"):
            return BOOL
        if expression.op not in ("add", "sub", "mul", "div", "mod", "bit_and", "bit_or", "bit_xor", "shl", "shr"):
            raise TypeError(f"unsupported typed operation {expression.op}")
        return a
    raise TypeError(f"unsupported typed expression {type(expression).__name__}")


def _normalize_bits(value: int, kind: ValueType) -> int | bool:
    if isinstance(kind, BoolType):
        return bool(value)
    value &= (1 << kind.width) - 1
    return value - (1 << kind.width) if kind.signed and value & (1 << (kind.width - 1)) else value


def _evaluate_typed(expression: XExpr, dut: Any, context: BitsType | None = None):
    kind = expression_type(expression, dut, context)
    if isinstance(expression, (BitsConstExpr, ConstantExpr)):
        value = expression.value
    elif isinstance(expression, (SignalExpr, BoundSignalExpr)):
        value = expression.evaluate(dut)
    elif isinstance(expression, CastExpr):
        value = _evaluate_typed(expression.operand, dut)
    elif isinstance(expression, UnaryExpr):
        operand = _evaluate_typed(expression.operand, dut)
        value = not bool(operand) if expression.op == "not" else ~operand if expression.op == "bit_not" else -operand
    elif isinstance(expression, BinaryExpr):
        a_type, b_type = _operand_types(expression, dut)
        a = _evaluate_typed(expression.left, dut, a_type if isinstance(a_type, BitsType) else None)
        if expression.op == "and" and not a:
            return False
        if expression.op == "or" and a:
            return True
        b = _evaluate_typed(expression.right, dut, b_type if isinstance(b_type, BitsType) else None)
        if expression.op in ("div", "mod"):
            if not b:
                raise ZeroDivisionError("typed expression division by zero")
            quotient = (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1)
            value = quotient if expression.op == "div" else a - quotient * b
        elif expression.op in ("shl", "shr"):
            if b < 0:
                raise ValueError("negative typed shift count")
            assert isinstance(kind, BitsType)
            value = (0 if expression.op == "shl" or a >= 0 else -1) if b >= kind.width else (a << b if expression.op == "shl" else a >> b)
        else:
            operations = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b,
                          "bit_and": lambda: a & b, "bit_or": lambda: a | b, "bit_xor": lambda: a ^ b,
                          "and": lambda: bool(b), "or": lambda: bool(b),
                          "eq": lambda: a == b, "ne": lambda: a != b, "lt": lambda: a < b,
                          "le": lambda: a <= b, "gt": lambda: a > b, "ge": lambda: a >= b}
            value = operations[expression.op]()
    else:
        raise TypeError(type(expression).__name__)
    return _normalize_bits(value, kind)


def as_expr(value: Any) -> XExpr:
    return value if isinstance(value, XExpr) else ConstantExpr(value)


def signal_expr(signal: Any) -> XExpr:
    """Reuse an IR node, or reference the original bound signal leaf.

    A shared expression builder can receive a symbolic path from @xtrigger
    or a live Bundle/XData leaf from an already bound interface.
    """
    if isinstance(signal, XExpr):
        return signal
    from .signals import as_xdata

    return BoundSignalExpr(as_xdata(signal))


def _read_bound_signal(signal: Any) -> Any:
    from .signals import read_signal
    from .events import LogicValue

    value = read_signal(signal)
    return value.as_int() if isinstance(value, LogicValue) and value.is_known else value


def _condition_known(expression: XExpr, dut: Any) -> bool:
    """Match the native predicate contract: dependent X/Z does not match."""
    from .events import LogicValue
    if isinstance(expression, (SignalExpr, BoundSignalExpr)):
        signal = (resolve_path(dut, expression.path) if isinstance(expression, SignalExpr)
                  else expression.signal)
        valid = getattr(signal, "DataValid", None)
        if callable(valid):
            return bool(valid())
        value = _read_bound_signal(signal)
        return not isinstance(value, LogicValue) or value.is_known
    if isinstance(expression, (UnaryExpr, CastExpr)):
        return _condition_known(expression.operand, dut)
    if isinstance(expression, BinaryExpr):
        return _condition_known(expression.left, dut) and _condition_known(expression.right, dut)
    return True


def _evaluate_condition(expression: XExpr, dut: Any) -> bool:
    return _condition_known(expression, dut) and bool(expression.evaluate(dut))


def resolve_path(root: Any, path: tuple[str, ...]) -> Any:
    value = root
    for name in path:
        value = getattr(value, name)
    return value


class SymbolicDut:
    def __getattr__(self, name: str) -> SignalExpr:
        return SignalExpr((name,))


@dataclass(frozen=True, slots=True)
class WaitStep:
    condition: XExpr


@dataclass(frozen=True, slots=True)
class WithinStep:
    minimum: int
    maximum: int
    condition: XExpr

    def __post_init__(self) -> None:
        if self.minimum < 0 or self.maximum < self.minimum:
            raise ValueError("Within requires 0 <= minimum <= maximum")


@dataclass(frozen=True, slots=True)
class HoldStep:
    condition: XExpr
    cycles: int

    def __post_init__(self) -> None:
        if self.cycles <= 0:
            raise ValueError("Hold cycles must be positive")


@dataclass(frozen=True, slots=True)
class NextStep:
    condition: XExpr


SequenceStep = WaitStep | WithinStep | HoldStep | NextStep


@dataclass(slots=True)
class _SequenceState:
    index: int = 0
    age: int = 0
    held: int = 0
    failed: bool = False
    expired: bool = False


def _advance_sequence(steps: tuple[SequenceStep, ...], state: _SequenceState,
                      evaluate: Callable[[XExpr], bool]) -> bool:
    """Shared by trigger and coverage runtimes; one step per sample."""
    step = steps[state.index]
    state.failed = False
    state.expired = False
    complete = False
    if isinstance(step, (WaitStep, NextStep)):
        complete = evaluate(step.condition)
        if isinstance(step, NextStep) and not complete:
            state.index = state.age = state.held = 0
            state.failed = True
    elif isinstance(step, WithinStep):
        state.age += 1
        if state.age > step.maximum:
            state.index = state.age = state.held = 0
            state.failed = True
            state.expired = True
            return False
        complete = state.age >= step.minimum and evaluate(step.condition)
    elif isinstance(step, HoldStep):
        state.held = state.held + 1 if evaluate(step.condition) else 0
        complete = state.held >= step.cycles
    if not complete:
        return False
    state.index += 1
    state.age = state.held = 0
    if state.index == len(steps):
        state.index = 0
        return True
    return False


@dataclass(frozen=True, slots=True)
class SequenceSpec:
    steps: tuple[SequenceStep, ...]

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("Sequence requires at least one step")


def Wait(condition: Any) -> WaitStep:
    return WaitStep(as_expr(condition))


def Next(condition: Any) -> NextStep:
    """Require the condition on the next sample, restarting on mismatch."""
    return NextStep(as_expr(condition))


def Within(minimum: int, maximum: int, condition: Any) -> WithinStep:
    return WithinStep(minimum, maximum, as_expr(condition))


def Hold(condition: Any, *, cycles: int) -> HoldStep:
    return HoldStep(as_expr(condition), cycles)


def Sequence(*steps: SequenceStep) -> SequenceSpec:
    return SequenceSpec(tuple(steps))


@dataclass(frozen=True, slots=True)
class FsmTransitionSpec:
    condition: XExpr | None
    target: str | None = None
    terminal: str | None = None


@dataclass(frozen=True, slots=True)
class FsmStateSpec:
    transitions: tuple[FsmTransitionSpec, ...]


@dataclass(frozen=True, slots=True)
class FsmSpec:
    start: str
    states: tuple[tuple[str, FsmStateSpec], ...]

    def __post_init__(self) -> None:
        names = {name for name, _ in self.states}
        if self.start not in names:
            raise ValueError(f"FSM start state does not exist: {self.start}")
        for state_name, state in self.states:
            for transition in state.transitions:
                if transition.target is not None and transition.target not in names:
                    raise ValueError(
                        f"FSM state {state_name} targets unknown state "
                        f"{transition.target}"
                    )


class State:
    def __init__(self) -> None:
        self._transitions: list[FsmTransitionSpec] = []

    def when(self, condition: Any) -> "_TransitionBuilder":
        return _TransitionBuilder(self, as_expr(condition))

    def otherwise(self) -> "_TransitionBuilder":
        return _TransitionBuilder(self, None)

    def _append(self, transition: FsmTransitionSpec) -> "State":
        if self._transitions and self._transitions[-1].condition is None:
            raise ValueError("unconditional FSM transition must be last")
        self._transitions.append(transition)
        return self

    def freeze(self) -> FsmStateSpec:
        return FsmStateSpec(tuple(self._transitions))


class _TransitionBuilder:
    def __init__(self, state: State, condition: XExpr | None) -> None:
        self._state = state
        self._condition = condition

    def goto(self, target: str) -> State:
        return self._state._append(
            FsmTransitionSpec(self._condition, target=target)
        )

    def trigger(self, terminal: str = "MATCHED") -> State:
        return self._state._append(
            FsmTransitionSpec(self._condition, terminal=terminal)
        )


def FSM(*, start: str, states: dict[str, State]) -> FsmSpec:
    if not states:
        raise ValueError("FSM requires at least one state")
    return FsmSpec(
        start=start,
        states=tuple((name, state.freeze()) for name, state in states.items()),
    )


# One portable representation of the existing IR, shared by schemas/contracts.
def program_to_dict(value):
    from dataclasses import fields, is_dataclass
    if isinstance(value, PythonPredicateExpr):
        return {"type": "PythonPredicateExpr", "identity": value.identity,
                "arguments": program_to_dict(value.arguments), "keywords": program_to_dict(value.keywords)}
    if isinstance(value, BoundSignalExpr):
        raise ValueError("portable patterns require symbolic signal paths")
    if is_dataclass(value) and type(value) in _PROGRAM_NODES.values():
        return {"type": type(value).__name__, **{f.name: program_to_dict(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, tuple):
        return [program_to_dict(item) for item in value]
    if type(value) in (int, bool, str, type(None)):
        return value
    raise TypeError(f"unsupported portable IR value: {type(value).__name__}")


def program_from_dict(value):
    if isinstance(value, list):
        return tuple(program_from_dict(item) for item in value)
    if isinstance(value, dict):
        kind = value.get("type")
        if kind not in _PROGRAM_NODES:
            raise ValueError(f"unknown IR node {kind!r}")
        return _PROGRAM_NODES[kind](**{k: program_from_dict(v) for k, v in value.items() if k != "type"})
    if type(value) not in (int, bool, str, type(None)):
        raise TypeError("invalid portable IR value")
    return value


def program_signal_paths(program):
    from dataclasses import fields, is_dataclass
    paths = set()
    def walk(value):
        if isinstance(value, SignalExpr):
            paths.add(value.path)
        elif isinstance(value, BoundSignalExpr):
            raise ValueError("coverage declarations require symbolic paths")
        elif is_dataclass(value):
            for f in fields(value):
                walk(getattr(value, f.name))
        elif isinstance(value, tuple):
            for item in value:
                walk(item)
    walk(program)
    return tuple(sorted(paths))


_PROGRAM_NODES = {cls.__name__: cls for cls in (
    ConstantExpr, SignalExpr, UnaryExpr, BinaryExpr, CastExpr, BitsConstExpr, PythonPredicateExpr, WaitStep, NextStep,
    WithinStep, HoldStep, SequenceSpec, FsmTransitionSpec, FsmStateSpec, FsmSpec,
)}
