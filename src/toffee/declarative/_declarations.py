"""Coverage v2 experimental declarations; these objects never count samples."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, is_dataclass
from enum import Enum
from typing import Generic, Self, TypeVar, get_type_hints, overload, TypeAlias

from ..coverage import Bin as CoreBin, BinSpec, BinKind, PatternMatcher, OverlapPolicy
from ..decorators import TriggerDefinition, PythonTriggerDefinition
from ..ir import FsmSpec
from ..data import Bundle

T = TypeVar("T")
S = TypeVar("S")
C = TypeVar("C", bound=type)


class DefinitionError(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        self.code, self.path = code, path
        super().__init__(f"{code} {path}: {message}")


class SampleTypeError(TypeError):
    pass


class CoverageReferenceError(ValueError):
    pass


def scalar_type(value_type: type) -> bool:
    return value_type in (int, bool, str) or (
        isinstance(value_type, type) and issubclass(value_type, Enum)
    )


def normalize(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


@dataclass(frozen=True, eq=False)
class BinRule(Generic[T]):
    spec: BinSpec
    value_type: type[T]
    _pattern: TriggerDefinition[T, ...] | PythonTriggerDefinition[T, ...] | None = None

    @property
    def at_least(self) -> int:
        return self.spec.at_least


PatternBin: TypeAlias = TriggerDefinition[T, ...] | PythonTriggerDefinition[T, ...] | BinRule[T]


class Bin:
    @staticmethod
    def pattern(pattern: TriggerDefinition[T, ...] | PythonTriggerDefinition[T, ...], *, at_least: int = 1,
                kind: BinKind = BinKind.NORMAL, terminals: tuple[str, ...] | None = None,
                all_terminals: bool = False, overlap: bool = False,
                max_active: int | None = None) -> BinRule[T]:
        if not isinstance(pattern, (TriggerDefinition, PythonTriggerDefinition)) or not isinstance(pattern.input_type, type):
            raise DefinitionError("E_PATTERN", "Bin.pattern", "use an annotated @xtrigger or @pytrigger definition")
        if overlap and max_active is None:
            raise DefinitionError("E_CAPACITY", "Bin.pattern", "overlap requires explicit max_active")
        if terminals is not None and (not terminals or all_terminals):
            raise DefinitionError("E_TERMINAL", "Bin.pattern", "select nonempty terminals or all_terminals")
        program = pattern.program
        if isinstance(program, FsmSpec):
            names = {t.terminal for _, st in program.states for t in st.transitions if t.terminal is not None}
            if terminals is None and not all_terminals and len(names) != 1:
                raise DefinitionError("E_TERMINAL", "Bin.pattern", "multi-terminal FSM requires explicit selection")
        elif terminals is not None or all_terminals:
            raise DefinitionError("E_TERMINAL", "Bin.pattern", "terminal selection requires FSM")
        matcher = PatternMatcher(program, pattern.mode.value, overlap, max_active if max_active is not None else 1,
                                 () if terminals is None else tuple(terminals))
        return BinRule(BinSpec(matcher, kind, at_least), pattern.input_type, pattern)

    @staticmethod
    def values(*values: T, at_least: int = 1) -> BinRule[T]:
        return Bin._values(values, False, True, at_least)

    @staticmethod
    def transition(*values: T, overlap: bool = True, at_least: int = 1) -> BinRule[T]:
        return Bin._values(values, True, overlap, at_least)

    @staticmethod
    def _values(values, temporal, overlap, at_least):
        if not values or not scalar_type(type(values[0])):
            raise DefinitionError("E_BIN_TYPE", "Bin", "use int, bool, str or a scalar Enum")
        value_type = type(values[0])
        if any(type(value) is not value_type for value in values):
            raise DefinitionError("E_BIN_TYPE", "Bin", "all values must have exactly the same type")
        raw = tuple(normalize(value) for value in values)
        if any(type(value) not in (int, bool, str) for value in raw):
            raise DefinitionError("E_BIN_TYPE", "Bin", "Enum values must be supported scalars")
        factory = CoreBin.transition if temporal else CoreBin.values
        kwargs = {"at_least": at_least, **({"overlap": overlap} if temporal else {})}
        return BinRule(factory(*raw, **kwargs), value_type)

    @staticmethod
    def range(low: int, high: int, *, at_least: int = 1) -> BinRule[int]:
        if type(low) is not int or type(high) is not int:
            raise DefinitionError("E_BIN_TYPE", "Bin.range", "integer endpoints required")
        return BinRule(CoreBin.range(low, high, at_least=at_least), int)

    @staticmethod
    def ranges(*ranges: tuple[int, int], at_least: int = 1) -> BinRule[int]:
        if any(type(low) is not int or type(high) is not int for low, high in ranges):
            raise DefinitionError("E_BIN_TYPE", "Bin.ranges", "integer endpoints required")
        return BinRule(CoreBin.ranges(*ranges, at_least=at_least), int)

    @staticmethod
    def masked(value: int, mask: int, *, width: int | None = None,
               at_least: int = 1) -> BinRule[int]:
        if type(value) is not int or type(mask) is not int:
            raise DefinitionError("E_BIN_TYPE", "Bin.masked", "integer value and mask required")
        return BinRule(CoreBin.masked(value, mask, width=width, at_least=at_least), int)

    @staticmethod
    def ignore(rule: BinRule[T]) -> BinRule[T]:
        return BinRule(CoreBin.ignore(rule.spec), rule.value_type, rule._pattern)

    @staticmethod
    def illegal(rule: BinRule[T]) -> BinRule[T]:
        return BinRule(CoreBin.illegal(rule.spec), rule.value_type, rule._pattern)

    @staticmethod
    def default(value_type: type[T]) -> BinRule[T]:
        if not scalar_type(value_type):
            raise DefinitionError("E_BIN_TYPE", "Bin.default", "unsupported value type")
        return BinRule(CoreBin.default(), value_type)


@dataclass(frozen=True)
class FieldRef(Generic[T]):
    root: type
    path: tuple[str, ...]
    value_type: type[T]


class _Path:
    def __init__(self, root, value_type, path=(), token=None):
        self._root, self._type, self._path = root, value_type, path
        self._token = token

    def __getattr__(self, name):
        if not (is_dataclass(self._type) or isinstance(self._type, type) and issubclass(self._type, Bundle)):
            raise DefinitionError("E_FIELD", ".".join(self._path), "use dataclass or annotated Bundle field chains")
        hints = get_type_hints(self._type)
        if name not in hints:
            raise DefinitionError("E_FIELD", ".".join((*self._path, name)), "field does not exist")
        return _Path(self._root, hints[name], (*self._path, name), self._token)

    def __bool__(self):
        raise DefinitionError("E_SELECTOR", ".".join(self._path), "field selectors cannot branch")

    def _comparison(self, other):
        raise DefinitionError("E_SELECTOR", ".".join(self._path), "field selectors cannot compare values")

    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = _comparison


class Fields(Generic[S]):
    def __init__(self, sample_type: type[S]) -> None:
        self.sample_type = sample_type

    def select(self, path: Callable[[S], T]) -> FieldRef[T]:
        token = object()
        try:
            result = path(_Path(self.sample_type, self.sample_type, token=token))
        except DefinitionError:
            raise
        except (TypeError, AttributeError) as error:
            raise DefinitionError("E_SELECTOR", self.sample_type.__name__,
                                  "use a plain dataclass field chain") from error
        if not isinstance(result, _Path) or result._token is not token or not result._path:
            raise DefinitionError("E_SELECTOR", self.sample_type.__name__,
                                  "selector must return a field from its own root")
        return FieldRef(result._root, result._path, result._type)

    @overload
    def signal(self, path: Callable[[S], object]) -> FieldRef[int]: ...
    @overload
    def signal(self, path: Callable[[S], object], *, value_type: type[T]) -> FieldRef[T]: ...
    def signal(self, path, *, value_type=int):
        """Select an existing Bundle signal and declare its sampled scalar type."""
        if not isinstance(self.sample_type, type) or not issubclass(self.sample_type, Bundle):
            raise DefinitionError("E_FIELD", self.sample_type.__name__, "signal selectors require an annotated Bundle")
        if value_type not in (int, bool):
            raise DefinitionError("E_FIELD_TYPE", self.sample_type.__name__, "live signals sample as int or bool")
        ref = self.select(path)
        return FieldRef(ref.root, ref.path, value_type)


@dataclass(frozen=True)
class Gate:
    field: FieldRef
    values: tuple
    negate: bool = False


class Iff:
    @staticmethod
    def equals(field: FieldRef[T], value: T) -> Gate:
        if type(value) is not field.value_type:
            raise DefinitionError("E_GATE_TYPE", ".".join(field.path), "gate value type differs from field")
        return Gate(field, (normalize(value),))

    @staticmethod
    def not_equals(field: FieldRef[T], value: T) -> Gate:
        gate = Iff.equals(field, value)
        return Gate(gate.field, gate.values, True)

    @staticmethod
    def truth(field: FieldRef[bool]) -> Gate:
        return Iff.equals(field, True)


class PointDeclaration:
    pass


@dataclass(frozen=True)
class BinSelection:
    point: PointDeclaration
    rule: BinRule


class BoundPoint(Generic[T]):
    def __init__(self, group, name):
        self._group, self._name = group, name

    def count(self, bin: PatternBin[T]) -> int:
        name = self._group._compiled.resolve_bin(self._name, bin)
        self._group.sync()
        with self._group.runtime._lock:
            return self._group.runtime._points[self._name].counts[name]

    @property
    def samples(self) -> int:
        self._group.sync()
        with self._group.runtime._lock:
            return self._group.runtime._points[self._name].samples

    @property
    def coverage(self) -> float:
        return self._group.runtime.point_coverage(self._name)


class CoverPoint(PointDeclaration, Generic[T]):
    def __init__(self, *, source: FieldRef[T] | None = None, iff: Gate | None = None,
                 weight: int = 1, goal: float = 100.0,
                 overlap: OverlapPolicy | None = None) -> None:
        if source is not None and not isinstance(source, FieldRef):
            raise DefinitionError("E_FIELD", type(self).__name__, "source must be a typed FieldRef")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "iff", iff)
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "goal", goal)
        object.__setattr__(self, "overlap", overlap)
        object.__setattr__(self, "_owners", [])

    def __setattr__(self, name, value):
        raise AttributeError("point declarations are immutable")

    def __set_name__(self, owner, name):
        self._owners.append((owner, name))

    @overload
    def __get__(self, instance: None, owner: type[object]) -> Self: ...
    @overload
    def __get__(self, instance: object, owner: type[object] | None = None) -> BoundPoint[T]: ...
    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return BoundPoint(instance, instance._compiled.resolve_point(self))

    def __set__(self, instance, value):
        raise AttributeError("coverage point views cannot be reassigned")

    def bin(self, bin: BinRule[T]) -> BinSelection:
        return BinSelection(self, bin)


class PatternCoverPoint(CoverPoint[T]):
    """Declaration marker; matching/counting use the existing coverage runtime."""


TemporalCoverPoint = PatternCoverPoint


class BoundCross:
    def __init__(self, group, name):
        self._group, self._name = group, name

    def count(self, *bins: BinSelection) -> int:
        import json
        expected = next(c.points for c in self._group.runtime.definition.crosses if c.name == self._name)
        resolved = tuple(self._group._compiled.resolve_point(item.point) for item in bins)
        if resolved != expected:
            raise CoverageReferenceError("cross selections must follow its point order and ownership")
        names = tuple(self._group._compiled.resolve_bin(slot, item.rule)
                      for slot, item in zip(resolved, bins))
        key = json.dumps(names, separators=(",", ":"), ensure_ascii=True)
        self._group.sync()
        with self._group.runtime._lock:
            if key not in self._group.runtime._crosses[self._name].counts:
                raise CoverageReferenceError("combination is not part of this cross")
            return self._group.runtime._crosses[self._name].counts[key]

    @property
    def coverage(self) -> float:
        return self._group.runtime.cross_coverage(self._name)

    @property
    def samples(self) -> int:
        self._group.sync()
        with self._group.runtime._lock:
            return self._group.runtime._crosses[self._name].samples


class Cross:
    def __init__(self, *points: PointDeclaration,
                 include: Sequence[tuple[BinSelection, ...]] | None = None,
                 ignore: Sequence[tuple[BinSelection, ...]] = (),
                 illegal: Sequence[tuple[BinSelection, ...]] = (),
                 iff: Gate | None = None, weight: int = 1, goal: float = 100.0,
                 max_auto_bins: int = 4096) -> None:
        object.__setattr__(self, "points", tuple(points))
        object.__setattr__(self, "include", None if include is None else tuple(map(tuple, include)))
        object.__setattr__(self, "ignore", tuple(map(tuple, ignore)))
        object.__setattr__(self, "illegal", tuple(map(tuple, illegal)))
        object.__setattr__(self, "iff", iff)
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "goal", goal)
        object.__setattr__(self, "max_auto_bins", max_auto_bins)

    def __setattr__(self, name, value):
        raise AttributeError("cross declarations are immutable")

    @overload
    def __get__(self, instance: None, owner: type[object]) -> Self: ...
    @overload
    def __get__(self, instance: object, owner: type[object] | None = None) -> BoundCross: ...
    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return BoundCross(instance, instance._compiled.resolve_cross(self))

    def __set__(self, instance, value):
        raise AttributeError("coverage cross views cannot be reassigned")


class CoverageFragment(Generic[S]):
    pass


@overload
def coverpoint(cls: C, /) -> C: ...
@overload
def coverpoint(*, description: str = "", overlap: OverlapPolicy = OverlapPolicy.WARN) -> Callable[[C], C]: ...
def coverpoint(cls=None, /, **options):
    def decorate(target):
        if not issubclass(target, CoverPoint):
            raise DefinitionError("E_DECORATOR", target.__name__, "inherit CoverPoint")
        if options.keys() - {"description", "overlap"}:
            raise DefinitionError("E_DECORATOR", target.__name__, "unknown coverpoint option")
        target._coverage_point_options = {**vars(target).get("_coverage_point_options", {}), **options}
        return target
    return decorate(cls) if cls is not None else decorate


@overload
def covergroup(cls: C, /) -> C: ...
@overload
def covergroup(*, schema_id: str | None = None, contract: str | None = None,
               description: str = "", iff: Gate | None = None) -> Callable[[C], C]: ...
def covergroup(cls=None, /, **options):
    def decorate(target):
        from ._runtime import CoverGroup
        if not issubclass(target, CoverGroup):
            raise DefinitionError("E_DECORATOR", target.__name__, "inherit CoverGroup")
        if options.keys() - {"schema_id", "contract", "description", "iff"}:
            raise DefinitionError("E_DECORATOR", target.__name__, "unknown covergroup option")
        target._coverage_group_options = {**vars(target).get("_coverage_group_options", {}), **options}
        return target
    return decorate(cls) if cls is not None else decorate


class SignalBindingBase:
    pass


@dataclass(frozen=True)
class SignalBinding(SignalBindingBase, Generic[T]):
    field: FieldRef[T]
    source: object
    source_id: str | None = None


def wire(field: FieldRef[T], source: object, *, source_id: str | None = None) -> SignalBinding[T]:
    return SignalBinding(field, source, source_id)
