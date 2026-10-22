from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from collections.abc import Mapping
from typing import Any, Generic, TypeVar

from .data import Bundle, DataNode, iter_data_leaves, normalize_data
from .events import XEvent
from .ir import signal_expr
from .signals import as_xdata
from .triggers import CompiledTrigger, DriveStable

T = TypeVar("T")


class Role(str, Enum):
    PRODUCER = "producer"
    CONSUMER = "consumer"
    MONITOR = "monitor"


@dataclass(frozen=True, slots=True)
class Transfer(Generic[T]):
    event: XEvent
    value: T

    @property
    def accepted_tick(self) -> int:
        return self.event.tick


@dataclass(frozen=True, slots=True)
class ReadyValid:
    clock: Any
    valid: Any
    ready: Any
    bits: DataNode
    role: Role | str = Role.MONITOR
    name: str = "ready_valid"
    validate_directions: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ReadyValid name cannot be empty")
        object.__setattr__(self, "clock", as_xdata(self.clock))
        object.__setattr__(self, "valid", as_xdata(self.valid))
        object.__setattr__(self, "ready", as_xdata(self.ready))
        object.__setattr__(self, "role", Role(self.role))
        object.__setattr__(
            self,
            "bits",
            normalize_data(self.bits, f"{self.name}.bits"),
        )
        if self.validate_directions and self.role is not Role.MONITOR:
            self._validate_directions()

    @classmethod
    def bind_tree(
        cls,
        root: Any,
        tree: Mapping[str, Any],
        *,
        clock: Any,
        source_prefix: str,
        role: Role | str = Role.MONITOR,
        name: str = "ready_valid",
        validate_directions: bool = True,
    ) -> "ReadyValid":
        """Bind an explicitly declared ready/valid signal-tree node.

        The caller selects the protocol type and subtree; this method validates
        its exact ``valid/ready/bits`` shape instead of inferring a protocol
        from arbitrary RTL names.
        """

        bound = Bundle.bind_tree(
            root,
            tree,
            source_prefix=source_prefix,
            path=name,
        )
        expected = {"valid", "ready", "bits"}
        actual = set(bound)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                f"{name} requires exactly valid/ready/bits; "
                f"missing={missing}, extra={extra}"
            )
        if not isinstance(bound.bits, Bundle):
            raise ValueError(f"{name}.bits must be an aggregate signal-tree node")
        return cls(
            clock=clock,
            valid=bound.valid,
            ready=bound.ready,
            bits=bound.bits,
            role=role,
            name=name,
            validate_directions=validate_directions,
        )

    @property
    def fire(self) -> CompiledTrigger:
        return CompiledTrigger(
            name=f"{self.name}.fire",
            dut=None,
            program=signal_expr(self.valid) & signal_expr(self.ready),
            sample=DriveStable(self.clock),
            mode="each_sample",
        )

    def as_role(self, role: Role | str) -> "ReadyValid":
        return replace(self, role=Role(role))

    def flipped(self) -> "ReadyValid":
        roles = {
            Role.PRODUCER: Role.CONSUMER,
            Role.CONSUMER: Role.PRODUCER,
            Role.MONITOR: Role.MONITOR,
        }
        # A flipped bound view describes the peer perspective over the same
        # leaves.  It must not mutate or revalidate DUT-relative IO metadata.
        return replace(
            self,
            role=roles[self.role],
            validate_directions=False,
        )

    def monitor_view(self) -> "ReadyValid":
        return replace(self, role=Role.MONITOR)

    def _validate_directions(self) -> None:
        bits = tuple(iter_data_leaves(self.bits, f"{self.name}.bits"))
        if self.role is Role.PRODUCER:
            driven = ((f"{self.name}.valid", self.valid),) + bits
            observed = ((f"{self.name}.ready", self.ready),)
        else:
            driven = ((f"{self.name}.ready", self.ready),)
            observed = ((f"{self.name}.valid", self.valid),) + bits
        for path, signal in driven:
            if _known_direction(signal) == "out":
                raise ValueError(
                    f"{path} is a DUT output and cannot be driven by "
                    f"role={self.role.value}"
                )
        for path, signal in observed:
            if _known_direction(signal) == "in":
                raise ValueError(
                    f"{path} is a DUT input and cannot be observed as the "
                    f"peer-driven side of role={self.role.value}"
                )


def _known_direction(signal: Any) -> str | None:
    if callable(getattr(signal, "IsBiIO", None)) and signal.IsBiIO():
        return "inout"
    if callable(getattr(signal, "IsInIO", None)) and signal.IsInIO():
        return "in"
    if callable(getattr(signal, "IsOutIO", None)) and signal.IsOutIO():
        return "out"
    return None


Decoupled = ReadyValid
