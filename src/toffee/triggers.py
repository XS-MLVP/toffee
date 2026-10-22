from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Generic, TypeVar

from .events import XEvent
from .ir import FsmSpec, SequenceSpec, XExpr

E = TypeVar("E", bound=XEvent)


class ConditionMode(str, Enum):
    ENTER = "enter"
    EACH_SAMPLE = "each_sample"
    CHANGE = "change"


def normalize_condition_mode(value: ConditionMode | str) -> ConditionMode:
    try:
        return ConditionMode(value)
    except ValueError as error:
        raise ValueError(
            "condition mode must be 'enter', 'each_sample', or 'change'"
        ) from error


class XTrigger(Generic[E]):
    simulation_bound = True

    def __await__(self):
        return self._wait().__await__()

    async def _wait(self) -> E:
        from ._context import current_reactor

        reactor = current_reactor()
        registration = reactor.register(self)
        try:
            return await registration.future
        finally:
            reactor.cancel(registration)


@dataclass(frozen=True, slots=True)
class PhaseTrigger(XTrigger[XEvent]):
    source: Any


@dataclass(frozen=True, slots=True)
class EdgeTrigger(PhaseTrigger):
    pass


@dataclass(frozen=True, slots=True)
class RisingEdge(EdgeTrigger):
    pass


@dataclass(frozen=True, slots=True)
class FallingEdge(EdgeTrigger):
    pass


@dataclass(frozen=True, slots=True)
class DriveStable(PhaseTrigger):
    pass


@dataclass(frozen=True, slots=True)
class ClockCycles(XTrigger[XEvent]):
    source: Any
    cycles: int

    def __post_init__(self) -> None:
        if self.cycles <= 0:
            raise ValueError("ClockCycles cycles must be positive")


@dataclass(frozen=True, slots=True)
class Value(XTrigger[XEvent]):
    signal: Any
    expected: Any
    sample: PhaseTrigger | None = None
    mode: ConditionMode | str = ConditionMode.ENTER

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_condition_mode(self.mode))


@dataclass(frozen=True, slots=True)
class ValueChange(XTrigger[XEvent]):
    signal: Any
    sample: PhaseTrigger | None = None


@dataclass(frozen=True, slots=True)
class SimTimeout(XTrigger[XEvent]):
    """Return a TIMEOUT event after rising cycles; do not raise an error."""

    cycles: int
    clock: Any

    def __post_init__(self) -> None:
        if self.cycles <= 0:
            raise ValueError("SimTimeout cycles must be positive")


@dataclass(frozen=True, slots=True)
class CompiledTrigger(XTrigger[XEvent]):
    name: str
    dut: Any
    program: XExpr | SequenceSpec | FsmSpec
    sample: PhaseTrigger | None = None
    mode: ConditionMode | str = ConditionMode.ENTER

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_condition_mode(self.mode))


@dataclass(frozen=True, slots=True)
class PythonPredicateTrigger(XTrigger[XEvent]):
    name: str
    predicate: Callable[[], bool]
    sample: PhaseTrigger | None = None
    mode: ConditionMode | str = ConditionMode.ENTER

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_condition_mode(self.mode))
