"""Fixed-stage, protocol-neutral multi-cycle input driver templates."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .async_drivers import AsyncDriver
from .data import DataNode, drive_data, iter_data_leaves, normalize_data
from .events import XEvent
from .signals import as_xdata, signal_identity
from .sync_drivers import SyncDriver
from .triggers import ClockCycles


RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


@dataclass(frozen=True, slots=True)
class DriveStage(Generic[RequestT]):
    """One fixed-duration use of a disjoint set of input signals.

    ``encode`` maps a request to values shaped like ``bits``. Stages are
    traversed in order; different requests may occupy different stages at
    the same time when ``max_active`` permits it.
    """

    name: str
    bits: DataNode
    idle: Any
    encode: Callable[[RequestT], Any]
    cycles: int = 1


def _prepare_stages(
    stages: Sequence[DriveStage[RequestT]],
) -> tuple[tuple[DriveStage[RequestT], DataNode], tuple[Any, ...]]:
    if not stages:
        raise ValueError("multi-cycle driver requires at least one stage")
    prepared: list[tuple[DriveStage[RequestT], DataNode]] = []
    signals: list[Any] = []
    names: set[str] = set()
    leaves: set[tuple[str, int]] = set()
    total_cycles = 0
    for stage in stages:
        if not isinstance(stage, DriveStage):
            raise TypeError("stages must contain DriveStage definitions")
        if not stage.name or not isinstance(stage.name, str) or stage.name in names:
            raise ValueError(f"invalid or duplicate stage name: {stage.name!r}")
        if not isinstance(stage.cycles, int) or isinstance(stage.cycles, bool) or stage.cycles <= 0:
            raise ValueError(f"{stage.name}: cycles must be a positive integer")
        if not callable(stage.encode):
            raise TypeError(f"{stage.name}: encode must be callable")
        bits = normalize_data(stage.bits, stage.name)
        stage_signals = tuple(signal for _, signal in iter_data_leaves(bits, stage.name))
        if not stage_signals:
            raise ValueError(f"{stage.name}: stage requires at least one signal")
        for signal in stage_signals:
            identity = signal_identity(signal)
            if identity in leaves:
                raise ValueError(f"{stage.name}: signal is used by multiple stages")
            leaves.add(identity)
        names.add(stage.name)
        signals.extend(stage_signals)
        total_cycles += stage.cycles
        prepared.append((stage, bits))
    if total_cycles < 2:
        raise ValueError("multi-cycle driver requires at least two input cycles")
    return tuple(prepared), tuple(signals)


class _StageDrive(Generic[RequestT]):
    clock: Any
    _stages: tuple[tuple[DriveStage[RequestT], DataNode], ...]

    def _drive_all_idle(self) -> None:
        for stage, bits in self._stages:
            drive_data(bits, stage.idle, stage.name)

    async def _drive_one(self, request: RequestT) -> XEvent:
        event: XEvent | None = None
        for stage, bits in self._stages:
            async with self.resource_lock(stage.name):
                try:
                    values = stage.encode(request)
                    drive_data(bits, values, stage.name)
                    event = await ClockCycles(self.clock, stage.cycles)
                finally:
                    drive_data(bits, stage.idle, stage.name)
        assert event is not None
        return event


class SyncMultiCycleDriver(_StageDrive[RequestT], SyncDriver[RequestT], Generic[RequestT]):
    """Await complete fixed-stage input acceptance; optionally overlap requests."""

    def __init__(
        self,
        clock: Any,
        stages: Sequence[DriveStage[RequestT]],
        *,
        name: str = "multi_cycle",
        max_active: int = 1,
    ) -> None:
        self.clock = as_xdata(clock)
        self._stages, signals = _prepare_stages(stages)
        self._initialized = False
        super().__init__(signals, name=name, max_active=max_active)

    def _ensure_idle(self) -> None:
        if not self._initialized:
            self._drive_all_idle()
            self._initialized = True

    async def __aenter__(self) -> SyncMultiCycleDriver[RequestT]:
        await super().__aenter__()
        try:
            self._ensure_idle()
        except BaseException:
            self.close()
            raise
        return self

    async def send(self, transaction: RequestT) -> XEvent:
        self._claim()
        try:
            self._ensure_idle()
        except BaseException:
            self.close()
            raise
        return await super().send(transaction)

    def close(self) -> None:
        super().close()
        self._initialized = False


class AsyncMultiCycleDriver(
    _StageDrive[RequestT], AsyncDriver[RequestT, ResponseT], Generic[RequestT, ResponseT]
):
    """Submit fixed-stage input immediately; optionally overlap requests."""

    def __init__(
        self,
        clock: Any,
        stages: Sequence[DriveStage[RequestT]],
        *,
        name: str = "multi_cycle",
        capacity: int = 64,
        max_active: int = 1,
    ) -> None:
        self.clock = as_xdata(clock)
        self._stages, signals = _prepare_stages(stages)
        super().__init__(signals, name=name, capacity=capacity, max_active=max_active)

    def start(self, execution: Any) -> AsyncMultiCycleDriver[RequestT, ResponseT]:
        super().start(execution)
        try:
            self._drive_all_idle()
        except BaseException:
            self.close()
            raise
        return self

    async def __aenter__(self) -> AsyncMultiCycleDriver[RequestT, ResponseT]:
        await super().__aenter__()
        try:
            self._drive_all_idle()
        except BaseException:
            self.close()
            raise
        return self
