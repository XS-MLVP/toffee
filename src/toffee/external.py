from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .events import XEvent, XEventKind, XPhase
from .triggers import XTrigger

T = TypeVar("T")
_external_ids = itertools.count(-1, -1)


def external_event(source: Any, value: Any = None) -> XEvent:
    return XEvent(
        event_id=next(_external_ids),
        tick=-1,
        phase=XPhase.EXTERNAL,
        kind=XEventKind.EXTERNAL,
        source=source,
        value=value,
    )


@dataclass(frozen=True, slots=True)
class AsyncioEventTrigger(XTrigger[XEvent]):
    event: asyncio.Event
    simulation_bound = False

    async def _wait(self) -> XEvent:
        await self.event.wait()
        return external_event(self.event)


@dataclass(frozen=True, slots=True)
class QueueTrigger(XTrigger[XEvent], Generic[T]):
    queue: asyncio.Queue[T]
    simulation_bound = False

    async def _wait(self) -> XEvent:
        value = await self.queue.get()
        return external_event(self.queue, value)


@dataclass(frozen=True, slots=True)
class TaskComplete(XTrigger[XEvent], Generic[T]):
    """Adapt a task's result without cancelling it when this wait is cancelled."""

    task: asyncio.Future[T]
    simulation_bound = False

    async def _wait(self) -> XEvent:
        value = await asyncio.shield(self.task)
        return external_event(self.task, value)


@dataclass(frozen=True, slots=True)
class WallTimeout(XTrigger[XEvent]):
    """Return a TIMEOUT event after wall-clock seconds; do not raise an error."""

    seconds: float
    simulation_bound = False

    def __post_init__(self) -> None:
        if self.seconds < 0:
            raise ValueError("WallTimeout seconds must be non-negative")

    async def _wait(self) -> XEvent:
        await asyncio.sleep(self.seconds)
        event = external_event(self)
        return XEvent(
            event_id=event.event_id,
            tick=-1,
            phase=XPhase.EXTERNAL,
            kind=XEventKind.TIMEOUT,
            source=self,
            value=self.seconds,
        )
