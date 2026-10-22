"""Awaited input drivers, independent of port shape and protocol."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections.abc import Callable, Hashable, Iterable
from typing import Any, Generic, TypeVar

from .components import SignalDriver
from ._driver_runtime import DriveControl
from ._cleanup import raise_cleanup_errors
from .data import DataNode, drive_data, iter_data_leaves, normalize_data
from .events import XEvent
from .signals import as_xdata
from .triggers import ClockCycles


RequestT = TypeVar("RequestT")


class SyncDriver(SignalDriver[RequestT], Generic[RequestT]):
    """Await input acceptance from ``send()`` without blocking the event loop.

    ``max_active=1`` keeps whole input transactions exclusive. A larger value
    lets concurrent send calls overlap, subject to resource arbitration in the
    concrete protocol driver. This is about submission semantics, not whether
    the implementation uses Python ``async def``.
    """

    def __init__(
        self,
        signals: Iterable[Any],
        *,
        name: str,
        max_active: int = 1,
    ) -> None:
        super().__init__(signals, name=name)
        self.max_active = max_active
        self._control = DriveControl(max_active)

    async def send(self, transaction: RequestT) -> XEvent:
        self._claim()
        return await self._control.run(self._drive_one, transaction)

    def resource_lock(self, resource: Hashable) -> asyncio.Lock:
        """Return the shared native asyncio lock for this resource."""

        return self._control.resource_lock(resource)

    @abstractmethod
    async def _drive_one(self, request: RequestT) -> XEvent:
        """Drive one complete input transaction and return its acceptance event."""

    def close(self) -> None:
        if self._control.calls:
            raise RuntimeError("sync driver has active input work")
        super().close()

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            self.close()
        except BaseException as error:
            raise_cleanup_errors(f"{self.name}: body and cleanup failures", [error], exc)


class SyncSingleCycleDriver(SyncDriver[RequestT], Generic[RequestT]):
    """Await one-cycle input acceptance on a scalar or aggregate data node."""

    def __init__(
        self,
        clock: Any,
        bits: DataNode,
        *,
        idle: Any,
        name: str = "single_cycle",
        encoder: Callable[[RequestT], Any] | None = None,
    ) -> None:
        self.clock = as_xdata(clock)
        self.bits = normalize_data(bits, name)
        self.idle = idle
        self.encoder = encoder
        super().__init__(
            (signal for _, signal in iter_data_leaves(self.bits, name)),
            name=name,
        )

    async def _drive_one(self, request: RequestT) -> XEvent:
        values = request if self.encoder is None else self.encoder(request)
        drive_data(self.bits, values, self.name)
        try:
            return await ClockCycles(self.clock, 1)
        finally:
            drive_data(self.bits, self.idle, self.name)
