"""Design-only interface. No runtime implementation or xreactor export exists.

The future namespace considered by the accompanying document is
``xreactor.declarative``. This standalone module is only a typing contract.
"""

from collections.abc import Callable, Sequence
from enum import Enum
from typing import Generic, Literal, Self, TypeVar, overload

T = TypeVar("T")
S = TypeVar("S")
C = TypeVar("C", bound=type[object])

@overload
def coverpoint(cls: C, /) -> C: ...
@overload
def coverpoint(
    *, description: str = "", overlap: OverlapPolicy = OverlapPolicy.WARN
) -> Callable[[C], C]: ...

@overload
def covergroup(cls: C, /) -> C: ...
@overload
def covergroup(
    *,
    schema_id: str | None = None,
    contract: str | None = None,
    description: str = "",
    iff: Gate | None = ...,
) -> Callable[[C], C]: ...

class IllegalPolicy(Enum):
    RAISE = "raise"
    RECORD = "record"

class OverlapPolicy(Enum):
    ALLOW = "allow"
    WARN = "warn"
    ERROR = "error"

class DefinitionError(ValueError): ...
class SampleTypeError(TypeError): ...
class CoverageReferenceError(ValueError): ...

class BinRule(Generic[T]):
    """Immutable rule identity, with no counter or mutable owner name."""
    @property
    def at_least(self) -> int: ...

class Bin:
    @staticmethod
    def values(*values: T, at_least: int = 1) -> BinRule[T]: ...
    @staticmethod
    def range(low: int, high: int, *, at_least: int = 1) -> BinRule[int]: ...
    @staticmethod
    def ranges(*ranges: tuple[int, int], at_least: int = 1) -> BinRule[int]: ...
    @staticmethod
    def masked(
        value: int, mask: int, *, width: int | None = None, at_least: int = 1
    ) -> BinRule[int]: ...
    @staticmethod
    def transition(
        *values: T, overlap: bool = True, at_least: int = 1
    ) -> BinRule[T]: ...
    @staticmethod
    def ignore(rule: BinRule[T]) -> BinRule[T]: ...
    @staticmethod
    def illegal(rule: BinRule[T]) -> BinRule[T]: ...
    @staticmethod
    def default(value_type: type[T]) -> BinRule[T]: ...

class FieldRef(Generic[T]):
    """Immutable checked path; also retains its root sample type at runtime."""
    @property
    def path(self) -> tuple[str, ...]: ...

class Fields(Generic[S]):
    def __init__(self, sample_type: type[S]) -> None: ...
    def select(self, path: Callable[[S], T]) -> FieldRef[T]: ...

class SignalBindingBase:
    """Common base for a heterogeneous sequence of immutable wire values."""
    ...

class SignalBinding(SignalBindingBase, Generic[T]):
    @property
    def field(self) -> FieldRef[T]: ...
    @property
    def source_id(self) -> str | None: ...

def wire(
    field: FieldRef[T], source: object, *, source_id: str | None = None
) -> SignalBinding[T]: ...

class Gate:
    """Serializable field comparison, never a per-sample Python callback."""
    ...

class Iff:
    @staticmethod
    def equals(field: FieldRef[T], value: T) -> Gate: ...
    @staticmethod
    def not_equals(field: FieldRef[T], value: T) -> Gate: ...
    @staticmethod
    def truth(field: FieldRef[bool]) -> Gate: ...

class PointDeclaration:
    """Common base allowing heterogeneous points in one Cross."""
    ...

class BinSelection:
    """A symbolic group point slot plus a bin in that point's lineage."""
    ...

class BoundPoint(Generic[T]):
    """Read-only view of a single group's point counters."""
    def count(self, bin: BinRule[T]) -> int: ...
    @property
    def samples(self) -> int: ...
    @property
    def coverage(self) -> float: ...

class CoverPoint(PointDeclaration, Generic[T]):
    def __init__(
        self,
        *,
        source: FieldRef[T] | None = None,
        iff: Gate | None = None,
        weight: int = 1,
        goal: float = 100.0,
        overlap: OverlapPolicy = ...,
    ) -> None: ...
    def bin(self, bin: BinRule[T]) -> BinSelection: ...
    @overload
    def __get__(self, instance: None, owner: type[object]) -> Self: ...
    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> BoundPoint[T]: ...
    def __set__(self, instance: object, value: object) -> None: ...

class BoundCross:
    def count(self, *bins: BinSelection) -> int: ...
    @property
    def samples(self) -> int: ...
    @property
    def coverage(self) -> float: ...

class Cross:
    def __init__(
        self,
        *points: PointDeclaration,
        include: Sequence[tuple[BinSelection, ...]] | None = None,
        ignore: Sequence[tuple[BinSelection, ...]] = (),
        illegal: Sequence[tuple[BinSelection, ...]] = (),
        iff: Gate | None = None,
        weight: int = 1,
        goal: float = 100.0,
        max_auto_bins: int = 4096,
    ) -> None: ...
    @overload
    def __get__(self, instance: None, owner: type[object]) -> Self: ...
    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> BoundCross: ...
    def __set__(self, instance: object, value: object) -> None: ...

class CompiledGroup(Generic[S]):
    """Immutable engine schema plus a serializable typed input contract."""
    @property
    def schema_digest(self) -> str: ...
    @property
    def input_digest(self) -> str: ...
    @property
    def sampling_contract(self) -> str: ...
    def schema_json(self) -> str: ...
    def input_contract_json(self) -> str: ...

class CoverageSnapshot:
    @property
    def samples(self) -> int: ...
    @property
    def coverage(self) -> float: ...
    def to_json(self) -> str: ...

class CompilationIssue:
    @property
    def code(self) -> str: ...
    @property
    def path(self) -> tuple[str, ...]: ...
    @property
    def message(self) -> str: ...

class BackendCapabilityError(NotImplementedError):
    @property
    def issues(self) -> tuple[CompilationIssue, ...]: ...

class ExecutionPlan:
    """Preflight explanation; owns no registrations or numeric engine roots."""
    @property
    def backend(self) -> Literal["native", "python"]: ...
    @property
    def native_eligible(self) -> bool: ...
    @property
    def required_abi(self) -> int: ...
    @property
    def issues(self) -> tuple[CompilationIssue, ...]: ...
    def to_json(self) -> str: ...

class CoverageFragment(Generic[S]):
    """Explicit declaration mixin, collected only in a concrete group."""
    ...

class CoverGroup(Generic[S]):
    def __init_subclass__(
        cls,
        *,
        schema_id: str | None = None,
        contract: str | None = None,
        iff: Gate | None = ...,
    ) -> None: ...
    def __init__(
        self,
        *,
        instance: str,
        illegal_policy: IllegalPolicy = IllegalPolicy.RAISE,
        run_id: str | None = None,
    ) -> None: ...
    @classmethod
    def compile(cls) -> CompiledGroup[S]: ...
    def bind(
        self,
        *,
        trigger: object,
        fields: Sequence[SignalBindingBase],
        strategy: Literal["auto", "native", "python"] = "auto",
        abort: object | None = None,
        accumulate: bool = False,
        overlap: bool = False,
        max_active: int | None = None,
    ) -> Self: ...
    def plan(self, backend: object) -> ExecutionPlan: ...
    def sync(self) -> None: ...
    def clear_history(self) -> None: ...
    def reset(self) -> None: ...
    def sample(self, sample: S) -> None: ...
    def snapshot(self) -> CoverageSnapshot: ...
