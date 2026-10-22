from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable

from .events import XEvent, XEventKind
from .external import external_event
from .triggers import XTrigger


@dataclass(frozen=True, slots=True)
class AnyOf(XTrigger[XEvent]):
    """Return the first event and cancel other waits.

    Wrap existing tasks in TaskComplete to preserve the source task and adapt
    its result to XEvent. A TIMEOUT event is returned, not raised.
    """

    triggers: tuple[Awaitable[XEvent], ...]
    simulation_bound = False

    def __init__(self, *triggers: Awaitable[XEvent]) -> None:
        if not triggers:
            raise ValueError("AnyOf requires at least one trigger")
        object.__setattr__(self, "triggers", tuple(triggers))

    async def _wait(self) -> XEvent:
        tasks = [asyncio.ensure_future(trigger) for trigger in self.triggers]
        try:
            done, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            # asyncio's done set is unordered. Argument order is the stable
            # tie-break when several sources complete in one loop turn.
            winner = next(task for task in tasks if task in done)
            return winner.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class AllOf(XTrigger[XEvent]):
    """Collect all events; cancel unfinished waits if one fails.

    TaskComplete protects an existing source task from this cancellation.
    """

    triggers: tuple[Awaitable[XEvent], ...]
    simulation_bound = False

    def __init__(self, *triggers: Awaitable[XEvent]) -> None:
        if not triggers:
            raise ValueError("AllOf requires at least one trigger")
        object.__setattr__(self, "triggers", tuple(triggers))

    async def _wait(self) -> XEvent:
        tasks = [asyncio.ensure_future(trigger) for trigger in self.triggers]
        try:
            causes = tuple(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        last = max(causes, key=lambda event: event.tick)
        event = external_event("AllOf", causes)
        return XEvent(
            event_id=event.event_id,
            tick=last.tick,
            phase=last.phase,
            kind=XEventKind.COMPOSITE,
            source="AllOf",
            causes=causes,
        )
