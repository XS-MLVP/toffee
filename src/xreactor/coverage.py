from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
from threading import RLock
from types import MappingProxyType, SimpleNamespace
from typing import Any, Iterable, Sequence
from uuid import uuid4
import warnings

from .events import LogicValue
from .ir import Next, Sequence as TemporalSequence, SignalExpr, Wait, _SequenceState, _advance_sequence


SCHEMA_VERSION = 1
Scalar = bool | int | str | None

# Event counters are cumulative; peak is a maximum. Live progress is never merged.
_DIAGNOSTIC_FIELDS = ("started", "completed", "failed", "expired", "aborted", "cleared", "peak_active")


def _empty_pattern_diagnostics():
    return dict.fromkeys((*_DIAGNOSTIC_FIELDS, "unfinished_at_close"), 0)


def _merge_diagnostics(left, right):
    if left is None:
        return _json_value(right)
    if right is None:
        return _json_value(left)
    result = {key: left[key] + right[key] for key in ("collected_runs", "uncollected_runs")}
    patterns = {}
    for item in (*left["patterns"], *right["patterns"]):
        key = item["kind"], item.get("point"), item.get("bin")
        if key not in patterns:
            patterns[key] = dict(item)
        else:
            for field in (*_DIAGNOSTIC_FIELDS, "unfinished_at_close"):
                a, b = patterns[key][field], item[field]
                patterns[key][field] = max(a, b) if field == "peak_active" else a + b
    result["patterns"] = list(patterns.values())
    return result


class BinKind(str, Enum):
    NORMAL = "normal"
    IGNORE = "ignore"
    ILLEGAL = "illegal"
    DEFAULT = "default"


class IllegalPolicy(str, Enum):
    RAISE = "raise"
    RECORD = "record"


class OverlapPolicy(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    ERROR = "error"


class CoverageSchemaError(ValueError):
    pass


class CoverageMergeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IllegalHit:
    group: str
    item: str
    bin_name: str
    value: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "item": self.item,
            "bin": self.bin_name,
            "value": _json_value(self.value),
            "metadata": _json_value(dict(self.metadata)),
        }


class IllegalBinError(AssertionError):
    def __init__(self, hits: Sequence[IllegalHit]) -> None:
        if not hits:
            raise ValueError("IllegalBinError requires at least one hit")
        self.hits = tuple(hits)
        first = self.hits[0]
        suffix = "" if len(self.hits) == 1 else f" (+{len(self.hits) - 1} more)"
        super().__init__(
            f"coverage group {first.group!r}, item {first.item!r}: "
            f"value {first.value!r} hit illegal bin {first.bin_name!r}{suffix}"
        )


def _validate_scalar(value: Any) -> Scalar:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Enum):
        enum_value = value.value
        if enum_value is None or isinstance(enum_value, (bool, int, str)):
            return enum_value
    raise CoverageSchemaError(
        f"coverage schema values must be bool/int/str/None, got {type(value).__name__}"
    )


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    return repr(value)


@dataclass(frozen=True, slots=True)
class ValueMatcher:
    values: tuple[Scalar, ...]

    def __post_init__(self) -> None:
        normalized = tuple(_validate_scalar(value) for value in self.values)
        if not normalized:
            raise CoverageSchemaError("value bin must contain at least one value")
        if len(set(normalized)) != len(normalized):
            raise CoverageSchemaError("value bin contains duplicate values")
        object.__setattr__(self, "values", normalized)

    def matches(self, value: Any) -> bool:
        if isinstance(value, Enum):
            value = value.value
        return value in self.values

    def to_dict(self) -> dict[str, Any]:
        return {"type": "values", "values": list(self.values)}


@dataclass(frozen=True, slots=True)
class RangeMatcher:
    ranges: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        normalized: list[tuple[int, int]] = []
        for low, high in self.ranges:
            if isinstance(low, bool) or isinstance(high, bool):
                raise CoverageSchemaError("range bounds must be integers, not bool")
            if not isinstance(low, int) or not isinstance(high, int):
                raise CoverageSchemaError("range bounds must be integers")
            if high < low:
                raise CoverageSchemaError(f"range [{low}:{high}] is backwards")
            normalized.append((low, high))
        if not normalized:
            raise CoverageSchemaError("range bin must contain at least one range")
        object.__setattr__(self, "ranges", tuple(normalized))

    def matches(self, value: Any) -> bool:
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        return any(low <= value <= high for low, high in self.ranges)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "ranges", "ranges": [list(item) for item in self.ranges]}


@dataclass(frozen=True, slots=True)
class WildcardMatcher:
    value: int
    mask: int
    width: int

    def __post_init__(self) -> None:
        if self.width <= 0:
            raise CoverageSchemaError("wildcard width must be positive")
        limit = (1 << self.width) - 1
        if self.value < 0 or self.mask < 0:
            raise CoverageSchemaError("wildcard value and mask must be non-negative")
        if self.value & ~limit or self.mask & ~limit:
            raise CoverageSchemaError("wildcard value/mask do not fit width")
        object.__setattr__(self, "value", self.value & self.mask)

    def matches(self, value: Any) -> bool:
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
        return (value & self.mask) == self.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "wildcard",
            "value": self.value,
            "mask": self.mask,
            "width": self.width,
        }


@dataclass(frozen=True, slots=True)
class DefaultMatcher:
    def matches(self, value: Any) -> bool:
        del value
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"type": "default"}


@dataclass(frozen=True, slots=True)
class TransitionMatcher:
    values: tuple[Scalar, ...]
    overlap: bool = True
    _program: Any = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        values = tuple(_validate_scalar(value) for value in self.values)
        if len(values) < 2:
            raise CoverageSchemaError("transition requires at least two samples")
        if not isinstance(self.overlap, bool):
            raise CoverageSchemaError("transition overlap must be bool")
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "_program", self.program(SignalExpr(("value",))))

    def program(self, source: Any) -> Any:
        return TemporalSequence(Wait(source == self.values[0]),
                                *(Next(source == value) for value in self.values[1:]))

    def advance(self, previous: tuple[_SequenceState, ...], value: Any, diagnostics=None
                ) -> tuple[bool, tuple[_SequenceState, ...]]:
        states = [replace(state) for state in previous]
        if self.overlap or not states:
            states.append(_SequenceState())
        program = self._program
        sample = SimpleNamespace(value=value)
        remaining = []
        hit = False
        for index, state in enumerate(states):
            new = index == len(previous)
            done = _advance_sequence(program.steps, state,
                                     lambda expr: bool(expr.evaluate(sample)))
            if diagnostics is not None:
                diagnostics["started"] += int(new and (done or state.index > 0))
                diagnostics["completed"] += int(done)
                diagnostics["failed"] += int(not new and state.failed)
            hit |= done
            if not done and not state.failed and state.index:
                remaining.append(state)
        if diagnostics is not None:
            diagnostics["peak_active"] = max(diagnostics["peak_active"], len(remaining))
        return hit, tuple(remaining)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "transition", "values": list(self.values), "overlap": self.overlap}


@dataclass(frozen=True, slots=True)
class PatternMatcher:
    program: Any
    mode: str = "enter"
    overlap: bool = False
    max_active: int = 1
    terminals: tuple[str, ...] = ()

    def __post_init__(self):
        from .ir import SequenceSpec, FsmSpec, XExpr, WaitStep, program_to_dict
        from .triggers import normalize_condition_mode
        object.__setattr__(self, "mode", normalize_condition_mode(self.mode).value)
        if not isinstance(self.program, (XExpr, SequenceSpec, FsmSpec)):
            raise CoverageSchemaError("pattern requires existing Expr/Sequence/FSM IR")
        program_to_dict(self.program)
        if type(self.overlap) is not bool or type(self.max_active) is not int or not 1 <= self.max_active <= (1 << 32)-1:
            raise CoverageSchemaError("invalid pattern overlap/capacity")
        if not self.overlap and self.max_active != 1:
            raise CoverageSchemaError("max_active > 1 requires overlap")
        if isinstance(self.program, XExpr) and (self.overlap or self.terminals):
            raise CoverageSchemaError("Expr cannot have overlap or terminals")
        if isinstance(self.program, (SequenceSpec, FsmSpec)) and self.mode != "enter":
            raise CoverageSchemaError("condition modes apply only to Expr patterns")
        if isinstance(self.program, SequenceSpec):
            if self.terminals or (self.overlap and not isinstance(self.program.steps[0], WaitStep)):
                raise CoverageSchemaError("invalid Sequence pattern options")
        if isinstance(self.program, FsmSpec):
            names = {t.terminal for _, st in self.program.states for t in st.transitions if t.terminal is not None}
            if not names or not set(self.terminals) <= names:
                raise CoverageSchemaError("unknown or missing FSM terminal")
        object.__setattr__(self, "terminals", tuple(self.terminals))

    def execution_key(self):
        """Process identity; terminal selection belongs to the consuming bin."""
        shape = self.to_dict()
        shape.pop("terminals")
        key = json.dumps(shape, sort_keys=True, separators=(",", ":"))
        from .ir import PythonPredicateExpr
        return (key, id(self.program._predicate)) if isinstance(self.program, PythonPredicateExpr) else key

    def matches(self, value):
        raise RuntimeError("pattern bins require Execution-owned sampling")

    def to_dict(self):
        from .ir import program_to_dict
        return dict(type="pattern", program=program_to_dict(self.program), mode=self.mode,
                    overlap=self.overlap, max_active=self.max_active, terminals=list(self.terminals))


Matcher = ValueMatcher | RangeMatcher | WildcardMatcher | DefaultMatcher | TransitionMatcher | PatternMatcher


def _matcher_from_dict(data: Mapping[str, Any]) -> Matcher:
    kind = data.get("type")
    if kind == "pattern":
        from .ir import program_from_dict
        return PatternMatcher(program_from_dict(data["program"]), data["mode"], data["overlap"], data["max_active"], tuple(data["terminals"]))
    if kind == "values":
        return ValueMatcher(tuple(data["values"]))
    if kind == "ranges":
        return RangeMatcher(tuple(tuple(item) for item in data["ranges"]))
    if kind == "wildcard":
        return WildcardMatcher(data["value"], data["mask"], data["width"])
    if kind == "default":
        return DefaultMatcher()
    if kind == "transition":
        return TransitionMatcher(tuple(data["values"]), data["overlap"])
    raise CoverageSchemaError(f"unknown matcher type {kind!r}")


@dataclass(frozen=True, slots=True)
class BinSpec:
    matcher: Matcher
    kind: BinKind = BinKind.NORMAL
    at_least: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", BinKind(self.kind))
        if self.at_least <= 0:
            raise CoverageSchemaError("bin at_least must be positive")
        if isinstance(self.matcher, DefaultMatcher) and self.kind is not BinKind.DEFAULT:
            raise CoverageSchemaError("default matcher must use default bin kind")
        if self.kind is BinKind.DEFAULT and not isinstance(self.matcher, DefaultMatcher):
            raise CoverageSchemaError("default bin kind requires default matcher")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "at_least": self.at_least,
            "matcher": self.matcher.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BinSpec":
        return cls(
            _matcher_from_dict(data["matcher"]),
            BinKind(data["kind"]),
            data["at_least"],
        )


class Bin:
    """Serializable factories for hardware functional-coverage bins."""

    @staticmethod
    def transition(*values: Scalar, overlap: bool = True, at_least: int = 1) -> BinSpec:
        return BinSpec(TransitionMatcher(tuple(values), overlap), at_least=at_least)

    @staticmethod
    def values(*values: Scalar, at_least: int = 1) -> BinSpec:
        return BinSpec(ValueMatcher(tuple(values)), at_least=at_least)

    @staticmethod
    def range(low: int, high: int, *, at_least: int = 1) -> BinSpec:
        return BinSpec(RangeMatcher(((low, high),)), at_least=at_least)

    @staticmethod
    def ranges(*ranges: tuple[int, int], at_least: int = 1) -> BinSpec:
        return BinSpec(RangeMatcher(tuple(ranges)), at_least=at_least)

    @staticmethod
    def masked(
        value: int,
        mask: int,
        *,
        width: int | None = None,
        at_least: int = 1,
    ) -> BinSpec:
        inferred = max(value.bit_length(), mask.bit_length(), 1)
        return BinSpec(
            WildcardMatcher(value, mask, inferred if width is None else width),
            at_least=at_least,
        )

    @staticmethod
    def default() -> BinSpec:
        return BinSpec(DefaultMatcher(), BinKind.DEFAULT)

    @staticmethod
    def ignore(spec: BinSpec) -> BinSpec:
        if spec.kind is BinKind.DEFAULT:
            raise CoverageSchemaError("default bin cannot be ignored")
        return replace(spec, kind=BinKind.IGNORE)

    @staticmethod
    def illegal(spec: BinSpec) -> BinSpec:
        if spec.kind is BinKind.DEFAULT:
            raise CoverageSchemaError("default bin cannot be illegal")
        return replace(spec, kind=BinKind.ILLEGAL)

    @staticmethod
    def array(
        prefix: str,
        low: int,
        high: int,
        *,
        count: int | None = None,
        at_least: int = 1,
    ) -> dict[str, BinSpec]:
        if not prefix:
            raise CoverageSchemaError("array-bin prefix must not be empty")
        if high < low:
            raise CoverageSchemaError("array-bin range is backwards")
        size = high - low + 1
        bins = size if count is None else count
        if bins <= 0:
            raise CoverageSchemaError("array-bin count must be positive")
        bins = min(bins, size)
        quotient, remainder = divmod(size, bins)
        result: dict[str, BinSpec] = {}
        start = low
        for index in range(bins):
            span = quotient + (1 if index < remainder else 0)
            stop = start + span - 1
            result[f"{prefix}[{index}]"] = Bin.range(
                start, stop, at_least=at_least
            )
            start = stop + 1
        return result


@dataclass(frozen=True, slots=True)
class Iff:
    source: str
    values: tuple[Scalar, ...] = (True,)
    invert: bool = False
    _source_parts: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.source:
            raise CoverageSchemaError("iff source must not be empty")
        normalized = tuple(_validate_scalar(value) for value in self.values)
        if not normalized:
            raise CoverageSchemaError("iff requires at least one accepted value")
        object.__setattr__(self, "values", normalized)
        object.__setattr__(self, "_source_parts", tuple(self.source.split(".")))

    @classmethod
    def equals(cls, source: str, value: Scalar) -> "Iff":
        return cls(source, (value,))

    @classmethod
    def not_equals(cls, source: str, value: Scalar) -> "Iff":
        return cls(source, (value,), True)

    def enabled(self, sample: Any) -> bool:
        value = _extract(sample, self._source_parts)
        if isinstance(value, LogicValue):
            if not value.is_known:
                return False
            value = value.as_int()
        if isinstance(value, Enum):
            value = value.value
        matched = value in self.values
        return not matched if self.invert else matched

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "values": list(self.values),
            "invert": self.invert,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Iff | None":
        if data is None:
            return None
        return cls(data["source"], tuple(data["values"]), data["invert"])


def _extract(sample: Any, source: str | tuple[str, ...]) -> Any:
    parts = (source,) if isinstance(source, str) and "." not in source else (
        tuple(source.split(".")) if isinstance(source, str) else source
    )
    value = sample
    for part in parts:
        if isinstance(value, dict):
            try:
                value = value[part]
            except KeyError as error:
                label = source if isinstance(source, str) else ".".join(source)
                raise KeyError(f"coverage sample has no field {label!r}") from error
        else:
            try:
                value = getattr(value, part)
            except AttributeError as error:
                if isinstance(value, Mapping):
                    try:
                        value = value[part]
                        continue
                    except KeyError:
                        label = source if isinstance(source, str) else ".".join(source)
                        raise KeyError(
                            f"coverage sample has no field {label!r}"
                        ) from error
                label = source if isinstance(source, str) else ".".join(source)
                raise AttributeError(
                    f"coverage sample has no attribute path {label!r}"
                ) from error
    return value


@dataclass(frozen=True, slots=True)
class NamedBin:
    name: str
    spec: BinSpec

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, **self.spec.to_dict()}


@dataclass(frozen=True, slots=True)
class CoverPointDef:
    name: str
    bins: Mapping[str, BinSpec]
    source: str | None = None
    iff: Iff | None = None
    weight: int = 1
    goal: float = 100.0
    overlap: OverlapPolicy | str = OverlapPolicy.WARN
    description: str = ""
    _named_bins: tuple[NamedBin, ...] = field(init=False, repr=False)
    _normal_bins: tuple[NamedBin, ...] = field(init=False, repr=False)
    _ignore_bins: tuple[NamedBin, ...] = field(init=False, repr=False)
    _illegal_bins: tuple[NamedBin, ...] = field(init=False, repr=False)
    _default_bins: tuple[NamedBin, ...] = field(init=False, repr=False)
    _excluded_bins: Mapping[str, str] = field(init=False, repr=False)
    _source_parts: tuple[str, ...] = field(init=False, repr=False)
    _normal_exact: Mapping[Scalar, tuple[str, ...]] = field(init=False, repr=False)
    _normal_dynamic: tuple[NamedBin, ...] = field(init=False, repr=False)
    _ignore_exact: Mapping[Scalar, tuple[str, ...]] = field(init=False, repr=False)
    _ignore_dynamic: tuple[NamedBin, ...] = field(init=False, repr=False)
    _illegal_exact: Mapping[Scalar, tuple[str, ...]] = field(init=False, repr=False)
    _illegal_dynamic: tuple[NamedBin, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise CoverageSchemaError("cover point name must not be empty")
        if not isinstance(self.description, str):
            raise CoverageSchemaError("cover point description must be a string")
        if self.weight < 0:
            raise CoverageSchemaError("cover point weight must not be negative")
        _validate_percentage(self.goal, "cover point goal")
        policy = OverlapPolicy(self.overlap)
        object.__setattr__(self, "overlap", policy)
        named = tuple(NamedBin(name, spec) for name, spec in self.bins.items())
        if not named:
            raise CoverageSchemaError(f"cover point {self.name!r} has no bins")
        names = [item.name for item in named]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise CoverageSchemaError(
                f"cover point {self.name!r} has empty or duplicate bin names"
            )
        if sum(item.spec.kind is BinKind.DEFAULT for item in named) > 1:
            raise CoverageSchemaError(
                f"cover point {self.name!r} has multiple default bins"
            )
        if not any(item.spec.kind is BinKind.NORMAL for item in named):
            raise CoverageSchemaError(
                f"cover point {self.name!r} requires a normal bin"
            )
        object.__setattr__(self, "bins", MappingProxyType(dict(self.bins)))
        object.__setattr__(self, "_named_bins", named)
        source = self.name if self.source is None else self.source
        object.__setattr__(self, "_source_parts", tuple(source.split(".")))
        from ._coverage_exclusions import excluded_normal_bins
        pattern = any(isinstance(item.spec.matcher, PatternMatcher) for item in named)
        if pattern and not all(isinstance(item.spec.matcher, PatternMatcher) for item in named):
            raise CoverageSchemaError("cannot mix value and pattern bins in a point")
        excluded = {} if pattern else excluded_normal_bins(named)
        object.__setattr__(self, "_excluded_bins", MappingProxyType(excluded))
        object.__setattr__(
            self,
            "_normal_bins",
            tuple(item for item in named if item.spec.kind is BinKind.NORMAL and item.name not in excluded),
        )
        if not self._normal_bins:
            raise CoverageSchemaError(f"cover point {self.name!r} has no eligible normal bins after exclusions")
        object.__setattr__(
            self,
            "_ignore_bins",
            tuple(item for item in named if item.spec.kind is BinKind.IGNORE),
        )
        object.__setattr__(
            self,
            "_illegal_bins",
            tuple(item for item in named if item.spec.kind is BinKind.ILLEGAL),
        )
        object.__setattr__(
            self,
            "_default_bins",
            tuple(item for item in named if item.spec.kind is BinKind.DEFAULT),
        )
        for prefix, selected in (
            ("normal", self._normal_bins),
            ("ignore", self._ignore_bins),
            ("illegal", self._illegal_bins),
        ):
            exact, dynamic = _compile_bin_matchers(selected)
            object.__setattr__(self, f"_{prefix}_exact", exact)
            object.__setattr__(self, f"_{prefix}_dynamic", dynamic)
        overlaps = _normal_overlaps(named)
        if overlaps:
            message = (
                f"cover point {self.name!r} has overlapping normal bins: "
                + ", ".join(f"{left}/{right}" for left, right in overlaps)
            )
            if policy is OverlapPolicy.ERROR:
                raise CoverageSchemaError(message)
            if policy is OverlapPolicy.WARN:
                warnings.warn(message, UserWarning, stacklevel=2)

    @property
    def is_pattern(self):
        return isinstance(self._named_bins[0].spec.matcher, PatternMatcher)

    @property
    def normal_bins(self) -> tuple[NamedBin, ...]:
        return self._normal_bins

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "source": self.name if self.source is None else self.source,
            "iff": None if self.iff is None else self.iff.to_dict(),
            "weight": self.weight,
            "goal": self.goal,
            "overlap": self.overlap.value,
            "bins": [item.to_dict() for item in sorted(self._named_bins, key=lambda x: x.name)],
        }
        if self.description:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverPointDef":
        bins = {
            item["name"]: BinSpec.from_dict(item)
            for item in data["bins"]
        }
        return cls(
            data["name"],
            bins,
            source=data["source"],
            iff=Iff.from_dict(data.get("iff")),
            weight=data["weight"],
            goal=data["goal"],
            overlap=data.get("overlap", "allow"),
            description=data.get("description", ""),
        )


@dataclass(frozen=True, slots=True)
class CrossDef:
    name: str
    points: tuple[str, ...]
    include: tuple[tuple[str, ...], ...] | None = None
    ignore: tuple[tuple[str, ...], ...] = ()
    illegal: tuple[tuple[str, ...], ...] = ()
    iff: Iff | None = None
    at_least: int = 1
    weight: int = 1
    goal: float = 100.0
    max_auto_bins: int = 4096
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise CoverageSchemaError("cross name must not be empty")
        if not isinstance(self.description, str):
            raise CoverageSchemaError("cross description must be a string")
        if len(self.points) < 2 or len(set(self.points)) != len(self.points):
            raise CoverageSchemaError("cross requires at least two distinct points")
        if self.at_least <= 0:
            raise CoverageSchemaError("cross at_least must be positive")
        if self.weight < 0:
            raise CoverageSchemaError("cross weight must not be negative")
        if self.max_auto_bins <= 0:
            raise CoverageSchemaError("max_auto_bins must be positive")
        _validate_percentage(self.goal, "cross goal")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "points": list(self.points),
            "include": None if self.include is None else [list(x) for x in self.include],
            "ignore": [list(x) for x in self.ignore],
            "illegal": [list(x) for x in self.illegal],
            "iff": None if self.iff is None else self.iff.to_dict(),
            "at_least": self.at_least,
            "weight": self.weight,
            "goal": self.goal,
            "max_auto_bins": self.max_auto_bins,
        }
        if self.description:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CrossDef":
        include = data.get("include")
        return cls(
            name=data["name"],
            points=tuple(data["points"]),
            include=None if include is None else tuple(tuple(x) for x in include),
            ignore=tuple(tuple(x) for x in data.get("ignore", ())),
            illegal=tuple(tuple(x) for x in data.get("illegal", ())),
            iff=Iff.from_dict(data.get("iff")),
            at_least=data["at_least"],
            weight=data["weight"],
            goal=data["goal"],
            max_auto_bins=data["max_auto_bins"],
            description=data.get("description", ""),
        )


@dataclass(frozen=True, slots=True)
class _ResolvedCross:
    definition: CrossDef
    eligible: tuple[tuple[str, ...], ...]
    eligible_set: frozenset[tuple[str, ...]]
    ignored: frozenset[tuple[str, ...]]
    illegal: frozenset[tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class CoverGroupDef:
    name: str
    points: tuple[CoverPointDef, ...]
    crosses: tuple[CrossDef, ...] = ()
    iff: Iff | None = None
    goal: float = 100.0
    description: str = ""
    _resolved_crosses: tuple[_ResolvedCross, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise CoverageSchemaError("cover group name must not be empty")
        if not isinstance(self.description, str):
            raise CoverageSchemaError("cover group description must be a string")
        _validate_percentage(self.goal, "cover group goal")
        if not self.points:
            raise CoverageSchemaError("cover group requires at least one point")
        point_map = _unique_by_name(self.points, "cover point")
        cross_map = _unique_by_name(self.crosses, "cross")
        if set(point_map) & set(cross_map):
            raise CoverageSchemaError("point and cross names must be distinct")
        if not any(item.weight > 0 for item in (*self.points, *self.crosses)):
            raise CoverageSchemaError("cover group requires a positive-weight item")
        if any(point_map[name].is_pattern for cross in self.crosses for name in cross.points if name in point_map):
            raise CoverageSchemaError("Cross requires shared sample context; pattern points cannot be crossed")
        resolved = tuple(_resolve_cross(item, point_map) for item in self.crosses)
        object.__setattr__(self, "_resolved_crosses", resolved)

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "version": 3 if any(p.is_pattern for p in self.points) else 2 if any(isinstance(bin.spec.matcher, TransitionMatcher)
                                for point in self.points for bin in point._named_bins) else SCHEMA_VERSION,
            "name": self.name,
            "iff": None if self.iff is None else self.iff.to_dict(),
            "goal": self.goal,
            "points": [item.to_dict() for item in sorted(self.points, key=lambda x: x.name)],
            "crosses": [item.to_dict() for item in sorted(self.crosses, key=lambda x: x.name)],
        }
        if self.description:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverGroupDef":
        version = data.get("version")
        if version not in (1, 2, 3):
            raise CoverageSchemaError(
                f"unsupported coverage schema version {version!r}"
            )
        return cls(
            name=data["name"],
            points=tuple(CoverPointDef.from_dict(item) for item in data["points"]),
            crosses=tuple(CrossDef.from_dict(item) for item in data.get("crosses", ())),
            iff=Iff.from_dict(data.get("iff")),
            goal=data["goal"],
            description=data.get("description", ""),
        )

    def instantiate(
        self,
        instance: str,
        *,
        illegal_policy: IllegalPolicy | str = IllegalPolicy.RAISE,
        run_id: str | None = None,
        run_metadata: Mapping[str, Any] | None = None,
        contract: str | None = None,
        capture: bool = True,
    ) -> "CoverGroup":
        if type(capture) is not bool:
            raise TypeError('capture must be a boolean')
        if not capture:
            from ._coverage_capture import suspended
            with suspended():
                return CoverGroup(self, instance, illegal_policy=illegal_policy,
                                  run_id=run_id, run_metadata=run_metadata, contract=contract)
        return CoverGroup(self, instance, illegal_policy=illegal_policy,
                          run_id=run_id, run_metadata=run_metadata, contract=contract)


def _validate_percentage(value: float, label: str) -> None:
    if not 0.0 <= value <= 100.0:
        raise CoverageSchemaError(f"{label} must be between 0 and 100")


def _unique_by_name(items: Iterable[Any], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if item.name in result:
            raise CoverageSchemaError(f"duplicate {label} name {item.name!r}")
        result[item.name] = item
    return result


def _resolve_cross(
    cross: CrossDef, points: Mapping[str, CoverPointDef]
) -> _ResolvedCross:
    try:
        dimensions = tuple(
            tuple(bin_.name for bin_ in points[name].normal_bins)
            for name in cross.points
        )
    except KeyError as error:
        raise CoverageSchemaError(
            f"cross {cross.name!r} references unknown point {error.args[0]!r}"
        ) from error
    declared = tuple({item.name for item in points[name]._named_bins
                      if item.spec.kind is BinKind.NORMAL} for name in cross.points)
    eligible_dimensions = tuple(set(names) for names in dimensions)
    if cross.include is None:
        size = 1
        for dimension in dimensions:
            size *= len(dimension)
        if size > cross.max_auto_bins:
            raise CoverageSchemaError(
                f"cross {cross.name!r} would create {size} bins, above "
                f"max_auto_bins={cross.max_auto_bins}; use explicit include"
            )
        selected = tuple(product(*dimensions))
    else:
        selected = cross.include
    illegal = frozenset(cross.illegal)
    ignored = frozenset(cross.ignore) - illegal
    for label, tuples in (
        ("include", selected),
        ("ignore", ignored),
        ("illegal", illegal),
    ):
        for item in tuples:
            if len(item) != len(cross.points) or any(name not in allowed
                                                    for name, allowed in zip(item, declared)):
                raise CoverageSchemaError(
                    f"cross {cross.name!r} has invalid {label} tuple {item!r}"
                )
    eligible = tuple(
        item for item in selected if item not in ignored and item not in illegal
        and all(name in eligible for name, eligible in zip(item, eligible_dimensions))
    )
    if len(set(eligible)) != len(eligible):
        raise CoverageSchemaError(f"cross {cross.name!r} has duplicate include bins")
    if not eligible:
        raise CoverageSchemaError(f"cross {cross.name!r} has no eligible bins")
    return _ResolvedCross(cross, tuple(sorted(eligible)), frozenset(eligible), ignored, illegal)


def _matcher_overlaps(left: Matcher, right: Matcher) -> bool:
    if isinstance(left, (TransitionMatcher, PatternMatcher)) or isinstance(right, (TransitionMatcher, PatternMatcher)):
        return False  # Temporal coexistence is intentional, not a static overlap.
    if isinstance(left, DefaultMatcher) or isinstance(right, DefaultMatcher):
        return False
    if isinstance(left, ValueMatcher):
        return any(right.matches(value) for value in left.values)
    if isinstance(right, ValueMatcher):
        return any(left.matches(value) for value in right.values)
    if isinstance(left, RangeMatcher) and isinstance(right, RangeMatcher):
        return any(
            max(a_low, b_low) <= min(a_high, b_high)
            for a_low, a_high in left.ranges
            for b_low, b_high in right.ranges
        )
    if isinstance(left, WildcardMatcher) and isinstance(right, WildcardMatcher):
        return ((left.value ^ right.value) & left.mask & right.mask) == 0
    wildcard = left if isinstance(left, WildcardMatcher) else right
    ranges = right if isinstance(left, WildcardMatcher) else left
    assert isinstance(wildcard, WildcardMatcher)
    assert isinstance(ranges, RangeMatcher)
    # Exact enumeration is bounded to keep schema finalization predictable.
    for low, high in ranges.ranges:
        if high - low <= 4096 and any(wildcard.matches(v) for v in range(low, high + 1)):
            return True
    return False


def _normal_overlaps(named: Sequence[NamedBin]) -> tuple[tuple[str, str], ...]:
    normal = [item for item in named if item.spec.kind is BinKind.NORMAL]
    # Physical-index matrices often have thousands of distinct value bins.
    # Invert exact scalar membership instead of comparing every bin pair.
    # Mixed/custom matchers retain the original general overlap algorithm.
    if all(type(item.spec.matcher) is ValueMatcher
           and all(type(value) in (type(None), bool, int, str)
                   for value in item.spec.matcher.values) for item in normal):
        owners: dict[Scalar, list[int]] = {}
        pairs: set[tuple[int, int]] = set()
        for index, item in enumerate(normal):
            for value in item.spec.matcher.values:
                previous = owners.setdefault(value, [])
                pairs.update((prior, index) for prior in previous)
                previous.append(index)
        return tuple((normal[left].name, normal[right].name) for left, right in sorted(pairs))
    result: list[tuple[str, str]] = []
    for index, left in enumerate(normal):
        for right in normal[index + 1 :]:
            if _matcher_overlaps(left.spec.matcher, right.spec.matcher):
                result.append((left.name, right.name))
    return tuple(result)


def _compile_bin_matchers(
    bins: Sequence[NamedBin],
) -> tuple[Mapping[Scalar, tuple[str, ...]], tuple[NamedBin, ...]]:
    exact: dict[Scalar, list[str]] = {}
    dynamic: list[NamedBin] = []
    for item in bins:
        matcher = item.spec.matcher
        if isinstance(matcher, ValueMatcher):
            for value in matcher.values:
                exact.setdefault(value, []).append(item.name)
        else:
            dynamic.append(item)
    return (
        MappingProxyType({value: tuple(names) for value, names in exact.items()}),
        tuple(dynamic),
    )


def _match_compiled(
    value: Any,
    exact: Mapping[Scalar, tuple[str, ...]],
    dynamic: Sequence[NamedBin],
    schema_order: Sequence[NamedBin],
    temporal: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    if isinstance(value, Enum):
        value = value.value
    try:
        matched = exact.get(value, ())
    except TypeError:
        matched = ()
    if not dynamic:
        return matched
    dynamic_matches = tuple(
        item.name for item in dynamic
        if (item.name in temporal if isinstance(item.spec.matcher, TransitionMatcher)
            else item.spec.matcher.matches(value))
    )
    selected = frozenset((*matched, *dynamic_matches))
    return tuple(item.name for item in schema_order if item.name in selected)


@dataclass(slots=True)
class _ItemStats:
    samples: int = 0
    gated: int = 0
    ignored: int = 0
    unmatched: int = 0
    unknown: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "gated": self.gated,
            "ignored": self.ignored,
            "unmatched": self.unmatched,
            "unknown": self.unknown,
            "counts": dict(sorted(self.counts.items())),
            "provenance": _json_value(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class CoverageSample:
    group: str
    point_bins: Mapping[str, tuple[str, ...]]
    cross_bins: Mapping[str, tuple[tuple[str, ...], ...]]
    gated: bool = False
    illegal_hits: tuple[IllegalHit, ...] = ()


@dataclass(frozen=True, slots=True)
class _PointPlan:
    point: CoverPointDef
    value: Any
    increments: tuple[str, ...] = ()
    normal: tuple[str, ...] = ()
    gated: bool = False
    ignored: bool = False
    unmatched: bool = False
    illegal: tuple[str, ...] = ()
    unknown: bool = False


@dataclass(frozen=True, slots=True)
class _CrossPlan:
    cross: _ResolvedCross
    increments: tuple[tuple[str, ...], ...] = ()
    normal: tuple[tuple[str, ...], ...] = ()
    gated: bool = False
    ignored: int = 0
    unmatched: int = 0
    illegal: tuple[tuple[str, ...], ...] = ()


class CoverGroup:
    """Mutable, thread-safe counters for one immutable covergroup schema."""

    def __init__(
        self,
        definition: CoverGroupDef,
        instance: str,
        *,
        illegal_policy: IllegalPolicy | str = IllegalPolicy.RAISE,
        run_id: str | None = None,
        run_metadata: Mapping[str, Any] | None = None,
        contract: str | None = None,
    ) -> None:
        if not instance:
            raise ValueError("coverage instance name must not be empty")
        self.definition = definition
        self.instance = instance
        for label, value in (("run_id", run_id), ("contract", contract)):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{label} must be a nonempty string")
        self._configured_run_id = run_id
        self._run_metadata = _json_value(dict(run_metadata or {}))
        self._new_origin()
        self.illegal_policy = IllegalPolicy(illegal_policy)
        self.samples = 0
        self.gated = 0
        self._points = {
            point.name: _ItemStats(counts={item.name: 0 for item in point._named_bins})
            for point in definition.points
        }
        self._crosses = {
            cross.definition.name: _ItemStats(
                counts={_tuple_key(item): 0 for item in cross.eligible}
                | {_tuple_key(item): 0 for item in cross.ignored}
                | {_tuple_key(item): 0 for item in cross.illegal}
            )
            for cross in definition._resolved_crosses
        }
        self._illegal_hits: list[IllegalHit] = []
        self._lock = RLock()
        self._history: dict[tuple[str, str], tuple[_SequenceState, ...]] = {}
        self._temporal_points = tuple(point for point in definition.points
                                      if any(isinstance(item.spec.matcher, TransitionMatcher)
                                             for item in point._named_bins))
        self._binding: Any = None
        self._collector: Any = None
        self._sampling_contract: Any = (None if contract is None else
                                        {"kind": "transaction", "name": contract})
        self._diagnostics: Any = None
        self._collection_complete = True
        from ._coverage_capture import register
        register(self)

    def _new_origin(self) -> None:
        self._origin_id = uuid4().hex
        self._origins = {self._origin_id: {
            "run_id": self._configured_run_id or self._origin_id,
            "instance": self.instance, "metadata": _json_value(self._run_metadata),
            "explicit_run_id": self._configured_run_id is not None,
        }}

    def _record_hit(self, stats: _ItemStats, name: str, count: int,
                    metadata: Mapping[str, Any] | None) -> None:
        evidence = stats.provenance.setdefault(name, {})
        meta = metadata
        current = evidence.get(self._origin_id)
        if current is None:
            evidence[self._origin_id] = {"count": count, "first": meta, "last": meta}
        else:
            current["count"] += count
            current["last"] = meta

    def bind(self, *, trigger: Any, fields: Mapping[str, Any], abort: Any = None,
             strategy: str = "auto", accumulate: bool = False,
             contract: str | None = None, overlap: bool = False,
             max_active: int | None = None, diagnostics: str = "off", root: Any = None) -> "CoverGroup":
        """Configure passive sampling; pass this instance to Execution(coverage=[...]).

        Each execution starts new matching history. By default counters are also
        reset; accumulate=True retains completed observations across executions.
        """
        from ._coverage_runtime import configure
        with self._lock:
            if self._collector is not None:
                raise RuntimeError("cannot rebind active coverage")
            self._binding = configure(trigger, fields, abort, strategy, accumulate, contract,
                                      overlap, max_active, diagnostics, root)
        return self

    def _start(self, execution: Any) -> None:
        from ._coverage_runtime import _Collector
        with self._lock:
            if self._collector is not None:
                raise RuntimeError("coverage already belongs to an Execution")
            if self._binding is None:
                raise RuntimeError("Execution coverage must be bound before starting")
            collector = _Collector(self, execution)
            collector.start()
            self._collector = collector

    def _stop(self) -> None:
        with self._lock:
            collector = self._collector
            if collector is not None:
                try:
                    collector.close()
                finally:
                    self._collector = None
                    self._history.clear()

    def sync(self) -> None:
        """Refresh the Python counters from the current native cumulative snapshot."""
        with self._lock:
            if self._collector is not None:
                self._collector.sync()

    def clear_history(self) -> None:
        """Discard incomplete patterns, retaining completed counts."""
        with self._lock:
            if self._collector is not None:
                self._collector.reset(counters=False)
            self._history.clear()

    def inspect(self) -> dict[str, Any]:
        """Read live pattern progress on demand without enabling a history log."""
        with self._lock:
            if self._collector is None:
                raise RuntimeError("coverage inspection requires an active Execution")
            return self._collector.inspect()

    def sample(
        self, sample: Any, *, metadata: Mapping[str, Any] | None = None,
        details: bool = True,
    ) -> CoverageSample | None:
        with self._lock:
            if self._collector is not None or (self._sampling_contract is not None
                                                and self._sampling_contract.get("kind") != "transaction"):
                raise RuntimeError("cannot manually sample coverage with an Execution sampling contract")
            return self._sample(sample, metadata=metadata, details=details)

    def _sample(
        self,
        sample: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
        details: bool = True,
        diagnostics: Any = None,
        pattern_hits: Any = None,
    ) -> CoverageSample | None:
        """Atomically sample one transaction.

        Set ``details=False`` on throughput-sensitive collectors that do not
        consume the per-sample hit description. Counters, gating, illegal-bin
        checks, and atomic commit semantics are unchanged.
        """

        with self._lock:
            if self.definition.iff is not None and not self.definition.iff.enabled(sample):
                self.gated += 1
                if diagnostics is not None:
                    for key, states in self._history.items():
                        diagnostics[key]["cleared"] += len(states)
                self._history.clear()
                if details:
                    return CoverageSample(self.instance, {}, {}, gated=True)
                return None

            history = {}
            planned_diagnostics = ({key: dict(value) for key, value in diagnostics.items()}
                                   if diagnostics is not None else None)
            matches = {}
            for point in self._temporal_points:
                enabled = point.iff is None or point.iff.enabled(sample)
                value = _extract(sample, point._source_parts) if enabled else None
                if isinstance(value, LogicValue):
                    enabled &= value.is_known
                    if value.is_known:
                        value = value.as_int()
                selected = set()
                for item in point._named_bins:
                    matcher = item.spec.matcher
                    if isinstance(matcher, TransitionMatcher):
                        key = (point.name, item.name)
                        stats = planned_diagnostics[key] if planned_diagnostics is not None else None
                        previous = self._history.get(key, ())
                        if not enabled and stats is not None:
                            stats["cleared"] += len(previous)
                        hit, states = (matcher.advance(previous, value, stats)
                                       if enabled else (False, ()))
                        history[key] = states
                        if hit:
                            selected.add(item.name)
                matches[point.name] = frozenset(selected)
            if pattern_hits is None and any(p.is_pattern for p in self.definition.points):
                raise RuntimeError("pattern points require Execution-owned sampling")
            point_plans = tuple(self._plan_pattern_point(point, sample, pattern_hits)
                                if point.is_pattern else self._plan_point(point, sample, matches.get(point.name, frozenset()))
                                for point in self.definition.points)
            plan_by_name = {plan.point.name: plan for plan in point_plans}
            cross_plans = tuple(
                self._plan_cross(cross, plan_by_name, sample)
                for cross in self.definition._resolved_crosses
            )

            illegal_hits: list[IllegalHit] = []
            has_illegal = any(plan.illegal for plan in point_plans) or any(
                plan.illegal for plan in cross_plans
            )
            meta = dict(metadata) if has_illegal and metadata is not None else {}
            for plan in point_plans:
                illegal_hits.extend(
                    IllegalHit(self.instance, plan.point.name, name, plan.value, meta)
                    for name in plan.illegal
                )
            for plan in cross_plans:
                illegal_hits.extend(
                    IllegalHit(
                        self.instance,
                        plan.cross.definition.name,
                        _tuple_label(item),
                        item,
                        meta,
                    )
                    for item in plan.illegal
                )

            # Commit only after all extraction and matching completed.
            normal_metadata = None if metadata is None else _json_value(dict(metadata))
            self.samples += 1
            self._history = history
            if diagnostics is not None:
                diagnostics.update(planned_diagnostics)
            for plan in point_plans:
                stats = self._points[plan.point.name]
                stats.unknown += int(plan.unknown)
                if plan.gated:
                    stats.gated += 1
                    continue
                stats.samples += 1
                stats.ignored += int(plan.ignored)
                stats.unmatched += int(plan.unmatched)
                for name in plan.increments:
                    stats.counts[name] += pattern_hits[(plan.point.name, name)] if plan.point.is_pattern else 1
                for name in plan.normal:
                    self._record_hit(stats, name, pattern_hits[(plan.point.name, name)] if plan.point.is_pattern else 1, normal_metadata)
            for plan in cross_plans:
                stats = self._crosses[plan.cross.definition.name]
                if plan.gated:
                    stats.gated += 1
                    continue
                stats.samples += 1
                stats.ignored += plan.ignored
                stats.unmatched += plan.unmatched
                for item in plan.increments:
                    stats.counts[_tuple_key(item)] += 1
                for item in plan.normal:
                    self._record_hit(stats, _tuple_key(item), 1, normal_metadata)
            self._illegal_hits.extend(illegal_hits)

            if illegal_hits and self.illegal_policy is IllegalPolicy.RAISE:
                raise IllegalBinError(illegal_hits)
            if not details:
                return None
            return CoverageSample(
                self.instance,
                MappingProxyType({plan.point.name: plan.normal for plan in point_plans}),
                MappingProxyType({
                    plan.cross.definition.name: plan.normal for plan in cross_plans
                }),
                illegal_hits=tuple(illegal_hits),
            )

    def _plan_pattern_point(self, point, sample, hits):
        if point.iff is not None and not point.iff.enabled(sample):
            return _PointPlan(point, 0, gated=True)
        names = tuple(item.name for item in point._named_bins if hits.get((point.name, item.name), 0))
        normal = tuple(name for name in names if point.bins[name].kind is BinKind.NORMAL)
        illegal = tuple(name for name in names if point.bins[name].kind is BinKind.ILLEGAL)
        ignored = any(point.bins[name].kind is BinKind.IGNORE for name in names)
        return _PointPlan(point, 0, increments=names, normal=normal, illegal=illegal, ignored=ignored, unmatched=not names)

    def _plan_point(self, point: CoverPointDef, sample: Any,
                    temporal: frozenset[str] = frozenset()) -> _PointPlan:
        if point.iff is not None and not point.iff.enabled(sample):
            return _PointPlan(point, None, gated=True)
        value = _extract(sample, point._source_parts)
        if isinstance(value, LogicValue):
            if not value.is_known:
                return _PointPlan(point, value, gated=True, unknown=True)
            value = value.as_int()
        illegal = _match_compiled(
            value,
            point._illegal_exact,
            point._illegal_dynamic,
            point._illegal_bins,
            temporal,
        )
        if illegal:
            return _PointPlan(point, value, increments=illegal, illegal=illegal)
        ignored = _match_compiled(
            value,
            point._ignore_exact,
            point._ignore_dynamic,
            point._ignore_bins,
            temporal,
        )
        if ignored:
            return _PointPlan(point, value, increments=ignored, ignored=True)
        normal = _match_compiled(
            value,
            point._normal_exact,
            point._normal_dynamic,
            point._normal_bins,
            temporal,
        )
        if normal:
            return _PointPlan(point, value, increments=normal, normal=normal)
        defaults = tuple(item.name for item in point._default_bins)
        return _PointPlan(
            point,
            value,
            increments=defaults,
            unmatched=not bool(defaults),
        )

    def _plan_cross(
        self,
        cross: _ResolvedCross,
        points: Mapping[str, _PointPlan],
        sample: Any,
    ) -> _CrossPlan:
        definition = cross.definition
        if definition.iff is not None and not definition.iff.enabled(sample):
            return _CrossPlan(cross, gated=True)
        dimensions = tuple(points[name].normal for name in definition.points)
        if any(not dimension for dimension in dimensions):
            return _CrossPlan(cross, unmatched=1)
        combinations = tuple(product(*dimensions))
        illegal = tuple(item for item in combinations if item in cross.illegal)
        ignored = tuple(item for item in combinations if item in cross.ignored)
        normal = tuple(
            item
            for item in combinations
            if item not in cross.illegal
            and item not in cross.ignored
            and item in cross.eligible_set
        )
        increments = (*illegal, *ignored, *normal)
        unmatched = len(combinations) - len(increments)
        return _CrossPlan(
            cross,
            increments=tuple(increments),
            normal=normal,
            ignored=len(ignored),
            unmatched=unmatched,
            illegal=illegal,
        )

    @property
    def illegal_hits(self) -> tuple[IllegalHit, ...]:
        self.sync()
        with self._lock:
            return tuple(self._illegal_hits)

    def point_coverage(self, name: str) -> float:
        self.sync()
        point = next((item for item in self.definition.points if item.name == name), None)
        if point is None:
            raise KeyError(name)
        with self._lock:
            stats = self._points[name]
            covered = sum(
                stats.counts[item.name] >= item.spec.at_least
                for item in point.normal_bins
            )
            return covered * 100.0 / len(point.normal_bins)

    def cross_coverage(self, name: str) -> float:
        self.sync()
        cross = next(
            (item for item in self.definition._resolved_crosses if item.definition.name == name),
            None,
        )
        if cross is None:
            raise KeyError(name)
        with self._lock:
            stats = self._crosses[name]
            covered = sum(
                stats.counts[_tuple_key(item)] >= cross.definition.at_least
                for item in cross.eligible
            )
            return covered * 100.0 / len(cross.eligible)

    @property
    def coverage(self) -> float:
        with self._lock:
            weighted = 0.0
            total_weight = 0
            for point in self.definition.points:
                if point.weight:
                    weighted += self.point_coverage(point.name) * point.weight
                    total_weight += point.weight
            for cross in self.definition._resolved_crosses:
                if cross.definition.weight:
                    weighted += (
                        self.cross_coverage(cross.definition.name)
                        * cross.definition.weight
                    )
                    total_weight += cross.definition.weight
            return weighted / total_weight

    @property
    def goal_met(self) -> bool:
        """Whether the raw aggregate percentage meets the group goal."""
        return self.coverage + 1e-12 >= self.definition.goal

    def _unmet_item_goals(self) -> tuple[str, ...]:
        return tuple(
            point.name for point in self.definition.points
            if point.weight and self.point_coverage(point.name) + 1e-12 < point.goal
        ) + tuple(
            cross.name for cross in self.definition.crosses
            if cross.weight and self.cross_coverage(cross.name) + 1e-12 < cross.goal
        )

    @property
    def covered(self) -> bool:
        """Coverage acceptance, including item goals, collection and illegal hits."""
        return (self.goal_met and not self._unmet_item_goals()
                and self._collection_complete and not self._illegal_hits)

    def uncovered(self) -> tuple[str, ...]:
        self.sync()
        result: list[str] = []
        with self._lock:
            for point in self.definition.points:
                stats = self._points[point.name]
                result.extend(
                    f"{point.name}.{item.name}"
                    for item in point.normal_bins
                    if stats.counts[item.name] < item.spec.at_least
                )
            for cross in self.definition._resolved_crosses:
                stats = self._crosses[cross.definition.name]
                result.extend(
                    f"{cross.definition.name}.{_tuple_label(item)}"
                    for item in cross.eligible
                    if stats.counts[_tuple_key(item)] < cross.definition.at_least
                )
        return tuple(result)

    def assert_coverage(self, minimum: float | None = None, *, per_item: bool = True) -> None:
        target = self.definition.goal if minimum is None else minimum
        _validate_percentage(target, "coverage minimum")
        actual = self.coverage
        if not self._collection_complete:
            raise AssertionError("coverage collection is incomplete")
        if self._illegal_hits:
            raise IllegalBinError(self._illegal_hits)
        if actual + 1e-12 < target:
            raise AssertionError(
                f"coverage instance {self.instance!r} is {actual:.2f}%, below "
                f"{target:.2f}%; uncovered bins: {', '.join(self.uncovered())}"
            )
        missing = self._unmet_item_goals() if per_item else ()
        if missing:
            raise AssertionError(f"coverage item goals not met: {', '.join(missing)}")

    def reset(self) -> None:
        with self._lock:
            if self._collector is not None:
                self._collector.reset(counters=True)
            self._history.clear()
            self.samples = 0
            self.gated = 0
            self._new_origin()
            for stats in (*self._points.values(), *self._crosses.values()):
                stats.samples = stats.gated = stats.ignored = stats.unmatched = stats.unknown = 0
                for name in stats.counts:
                    stats.counts[name] = 0
                stats.provenance.clear()
            self._illegal_hits.clear()
            self._collection_complete = True
            self._diagnostics = None
            if self._collector is not None:
                self._collector.publish_diagnostics()

    def report(self) -> dict[str, Any]:
        self.sync()
        with self._lock:
            points = []
            for point in self.definition.points:
                stats = self._points[point.name]
                item = stats.to_dict()
                item.update({
                    "name": point.name,
                    "coverage": self.point_coverage(point.name),
                    "goal": point.goal,
                    "goal_met": self.point_coverage(point.name) + 1e-12 >= point.goal,
                    "excluded_bins": dict(point._excluded_bins),
                })
                points.append(item)
            crosses = []
            for cross in self.definition._resolved_crosses:
                stats = self._crosses[cross.definition.name]
                item = stats.to_dict()
                item.update({
                    "name": cross.definition.name,
                    "coverage": self.cross_coverage(cross.definition.name),
                    "goal": cross.definition.goal,
                    "goal_met": self.cross_coverage(cross.definition.name) + 1e-12 >= cross.definition.goal,
                    "bins": [
                        {"id": _tuple_key(names), "tuple": list(names),
                         "label": _tuple_label(names), "kind": kind,
                         "at_least": cross.definition.at_least}
                        for kind, tuples in (("normal", cross.eligible),
                                             ("ignore", sorted(cross.ignored)),
                                             ("illegal", sorted(cross.illegal)))
                        for names in tuples
                    ],
                })
                crosses.append(item)
            result = {
                "report_version": 2,
                "schema": self.definition.to_dict(),
                "schema_digest": self.definition.digest,
                "instance": self.instance,
                "illegal_policy": self.illegal_policy.value,
                "samples": self.samples,
                "gated": self.gated,
                "coverage": self.coverage,
                "goal": self.definition.goal,
                "covered": self.covered,
                "goal_met": self.goal_met,
                "items_goal_met": not self._unmet_item_goals(),
                "has_illegal": bool(self._illegal_hits),
                "points": points,
                "crosses": crosses,
                "illegal_hits": [item.to_dict() for item in self._illegal_hits],
                "sampling_contract": _json_value(self._sampling_contract),
                "sampling_backend": getattr(self, "_sampling_backend", "manual"),
                "sampling_fallback": getattr(self, "_sampling_fallback", None),
                "diagnostics": _json_value(self._diagnostics),
                "collection_complete": self._collection_complete,
                "origins": _json_value(self._origins),
                "active_origin": self._origin_id,
            }
            result["snapshot_id"] = sha256(json.dumps(result, sort_keys=True,
                                                       separators=(",", ":")).encode()).hexdigest()
            return result

    @classmethod
    def from_report(cls, report: Mapping[str, Any]) -> "CoverGroup":
        if report.get("report_version") != 2:
            raise CoverageMergeError("unsupported coverage report version")
        payload = {key: value for key, value in report.items() if key != "snapshot_id"}
        digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest != report.get("snapshot_id"):
            raise CoverageMergeError("coverage snapshot digest is invalid")
        definition = CoverGroupDef.from_dict(report["schema"])
        if definition.digest != report["schema_digest"]:
            raise CoverageMergeError("coverage report schema digest is invalid")
        ordered = {}
        for kind, definitions in (("points", definition.points), ("crosses", definition.crosses)):
            expected = {item.name: item for item in definitions}
            names = [item["name"] for item in report[kind]]
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise CoverageMergeError("coverage report items do not match schema")
            ordered[kind] = tuple(expected[name] for name in names)
        definition = replace(definition, **ordered)
        from ._coverage_capture import suspended
        with suspended():
            group = cls(
                definition,
                report["instance"],
                illegal_policy=report.get("illegal_policy", "raise"),
            )
        group.samples = report["samples"]
        group._sampling_contract = _json_value(report.get("sampling_contract"))
        group._sampling_backend = report.get("sampling_backend", "manual")
        group._sampling_fallback = report.get("sampling_fallback")
        group._diagnostics = _json_value(report.get("diagnostics"))
        group._collection_complete = report.get("collection_complete", True)
        group.gated = report["gated"]
        group._origins = _json_value(report["origins"])
        group._origin_id = report["active_origin"]
        if group._origin_id not in group._origins:
            raise CoverageMergeError("coverage active origin is invalid")
        for origin in group._origins.values():
            if not isinstance(origin.get("run_id"), str) or not origin["run_id"]:
                raise CoverageMergeError("coverage run identity is invalid")
        active = group._origins[group._origin_id]
        group._configured_run_id = active.get("run_id") if active.get("explicit_run_id") else None
        group._run_metadata = active.get("metadata", {})
        for item in report["points"]:
            group._restore_stats(group._points[item["name"]], item)
        for item in report["crosses"]:
            group._restore_stats(group._crosses[item["name"]], item)
        counters = [group.samples, group.gated]
        for stats in (*group._points.values(), *group._crosses.values()):
            counters.extend((stats.samples, stats.gated, stats.ignored, stats.unmatched, stats.unknown))
            counters.extend(stats.counts.values())
            for name, evidence in stats.provenance.items():
                if name not in stats.counts or not set(evidence).issubset(group._origins):
                    raise CoverageMergeError("coverage evidence identity does not match counters/origins")
                counts = [record["count"] for record in evidence.values()]
                if any(type(count) is not int or count <= 0 for count in counts):
                    raise CoverageMergeError("coverage evidence counts must be positive integers")
                if sum(counts) > stats.counts[name]:
                    raise CoverageMergeError("coverage evidence exceeds bin hit count")
        if any(type(count) is not int or count < 0 for count in counters):
            raise CoverageMergeError("coverage counters must be nonnegative integers")
        group._illegal_hits = [
            IllegalHit(
                item["group"],
                item["item"],
                item["bin"],
                item["value"],
                item.get("metadata", {}),
            )
            for item in report.get("illegal_hits", ())
        ]
        return group

    @staticmethod
    def _restore_stats(stats: _ItemStats, data: Mapping[str, Any]) -> None:
        if set(stats.counts) != set(data["counts"]):
            raise CoverageMergeError("coverage report counters do not match schema")
        stats.samples = data["samples"]
        stats.gated = data["gated"]
        stats.ignored = data["ignored"]
        stats.unmatched = data["unmatched"]
        stats.unknown = data.get("unknown", 0)
        stats.counts.update(data["counts"])
        stats.provenance = _json_value(data.get("provenance", {}))

    def merge(self, other: "CoverGroup", *, require_instance: bool = True) -> None:
        snapshot = other.report()
        with self._lock:
            self._validate_merge(snapshot, require_instance=require_instance)
            self._merge_snapshot(snapshot)

    def _validate_merge(self, snapshot: Mapping[str, Any], *, require_instance: bool = True) -> None:
        if self.definition.digest != snapshot["schema_digest"]:
            raise CoverageMergeError(
                f"schema mismatch merging {self.instance!r} and {snapshot['instance']!r}"
            )
        if require_instance and self.instance != snapshot["instance"]:
            raise CoverageMergeError(
                f"instance mismatch {self.instance!r} != {snapshot['instance']!r}"
            )
        if self._collector is not None:
            raise CoverageMergeError("cannot merge into active coverage")
        if self._sampling_contract != snapshot.get("sampling_contract"):
            raise CoverageMergeError("coverage sampling contract mismatch")
        incoming = snapshot.get("origins", {})
        if not self._origins or not incoming:
            raise CoverageMergeError("coverage run identity is missing")
        existing_runs = {(origin["instance"], origin["run_id"]) for origin in self._origins.values()}
        incoming_runs = {(origin["instance"], origin["run_id"]) for origin in incoming.values()}
        if set(self._origins) & set(incoming) or existing_runs & incoming_runs:
            raise CoverageMergeError("duplicate or overlapping coverage run/snapshot")

    def _merge_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        with self._lock:
            self._origins.update(_json_value(snapshot["origins"]))
            if getattr(self, "_sampling_backend", "manual") != snapshot.get("sampling_backend", "manual"):
                self._sampling_backend = "mixed"
            reasons = {reason for reason in (getattr(self, "_sampling_fallback", None),
                                              snapshot.get("sampling_fallback")) if reason}
            self._sampling_fallback = "; ".join(sorted(reasons)) or None
            self.samples += snapshot["samples"]
            self._diagnostics = _merge_diagnostics(self._diagnostics, snapshot.get("diagnostics"))
            self._collection_complete &= snapshot.get("collection_complete", True)
            self.gated += snapshot["gated"]
            for item in snapshot["points"]:
                _add_stats(self._points[item["name"]], item)
            for item in snapshot["crosses"]:
                _add_stats(self._crosses[item["name"]], item)
            self._illegal_hits.extend(
                IllegalHit(
                    item["group"], item["item"], item["bin"], item["value"],
                    item.get("metadata", {}),
                )
                for item in snapshot["illegal_hits"]
            )


def _add_stats(stats: _ItemStats, data: Mapping[str, Any]) -> None:
    if set(stats.counts) != set(data["counts"]):
        raise CoverageMergeError("coverage counters do not match schema")
    stats.samples += data["samples"]
    stats.gated += data["gated"]
    stats.ignored += data["ignored"]
    stats.unmatched += data["unmatched"]
    stats.unknown += data.get("unknown", 0)
    for name, count in data["counts"].items():
        stats.counts[name] += count
    for name, evidence in data.get("provenance", {}).items():
        stats.provenance.setdefault(name, {}).update(_json_value(evidence))


def _tuple_key(item: tuple[str, ...]) -> str:
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def _tuple_label(item: tuple[str, ...]) -> str:
    return " × ".join(item)


class CoverageDatabase:
    def __init__(self, groups: Iterable[CoverGroup] = ()) -> None:
        self._groups: dict[str, CoverGroup] = {}
        self._lock = RLock()
        for group in groups:
            self.add(group)

    def add(self, group: CoverGroup) -> CoverGroup:
        with self._lock:
            if group.instance in self._groups:
                raise ValueError(f"duplicate coverage instance {group.instance!r}")
            self._groups[group.instance] = group
        return group

    def __getitem__(self, instance: str) -> CoverGroup:
        return self._groups[instance]

    @property
    def groups(self) -> Mapping[str, CoverGroup]:
        with self._lock:
            return MappingProxyType(dict(self._groups))

    @property
    def coverage(self) -> float:
        with self._lock:
            if not self._groups:
                return 0.0
            return sum(group.coverage for group in self._groups.values()) / len(self._groups)

    def merge(self, other: "CoverageDatabase") -> None:
        incoming = other.report()["groups"]
        with self._lock:
            prepared = []
            for item in incoming:
                instance = item["instance"]
                group = CoverGroup.from_report(item)
                if instance in self._groups:
                    self._groups[instance]._validate_merge(item)
                prepared.append((instance, group))
            for instance, group in prepared:
                if instance in self._groups:
                    self._groups[instance].merge(group)
                else:
                    self._groups[instance] = group

    def type_reports(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            grouped: dict[str, CoverGroup] = {}
            for group in self._groups.values():
                digest = group.definition.digest
                if digest not in grouped:
                    grouped[digest] = CoverGroup.from_report(group.report())
                    grouped[digest].instance = f"type:{group.definition.name}"
                else:
                    grouped[digest].merge(group, require_instance=False)
            return tuple(group.report() for group in grouped.values())

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "format": "xreactor-functional-coverage",
                "version": 2,
                "coverage": self.coverage,
                "covered": bool(self._groups) and all(group.covered for group in self._groups.values()),
                "groups": [group.report() for group in self._groups.values()],
            }

    def write_json(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.report(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return destination

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverageDatabase":
        if data.get("format") != "xreactor-functional-coverage" or data.get("version") != 2:
            raise CoverageMergeError("unsupported functional coverage database format")
        return cls(CoverGroup.from_report(item) for item in data["groups"])

    @classmethod
    def read_json(cls, path: str | Path) -> "CoverageDatabase":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
