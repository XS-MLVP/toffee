from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class XPhase(Enum):
    FALLING_STABLE = auto()
    DRIVE_STABLE = auto()
    RISING_STABLE = auto()
    EXTERNAL = auto()


class XEventKind(Enum):
    CLOCK_FALL = auto()
    CLOCK_RISE = auto()
    DRIVE_STABLE = auto()
    CLOCK_CYCLES = auto()
    VALUE = auto()
    VALUE_CHANGE = auto()
    CONDITION = auto()
    FSM = auto()
    TIMEOUT = auto()
    EXTERNAL = auto()
    COMPOSITE = auto()
    BACKEND_STOP = auto()


@dataclass(frozen=True, slots=True)
class LogicValue:
    """A four-state value encoded as SystemVerilog ``aval``/``bval``.

    For each bit, ``x_mask=0`` means ``value`` contains 0/1; with
    ``x_mask=1``, value 0 denotes Z and value 1 denotes X.
    """

    value: int
    x_mask: int
    width: int

    @property
    def is_known(self) -> bool:
        return self.x_mask == 0

    def as_int(self, *, strict: bool = True) -> int:
        if strict and not self.is_known:
            raise ValueError("logic value contains X/Z bits")
        return self.value & ((1 << self.width) - 1)

    def __int__(self) -> int:
        return self.as_int()


@dataclass(frozen=True, slots=True)
class XEvent:
    event_id: int
    tick: int
    phase: XPhase
    kind: XEventKind
    source: Any = None
    value: Any = None
    causes: tuple["XEvent", ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class EdgeEvent(XEvent):
    pass


@dataclass(frozen=True, slots=True)
class PhaseEvent(XEvent):
    pass


@dataclass(frozen=True, slots=True)
class ConditionEvent(XEvent):
    pass


@dataclass(frozen=True, slots=True)
class FsmEvent(XEvent):
    terminal_state: str = "MATCHED"
