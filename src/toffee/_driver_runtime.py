"""Shared input scheduling mechanics; no signal or protocol policy."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

from .events import XEvent


class DriveControl:
    def __init__(self, max_active: int) -> None:
        if max_active <= 0:
            raise ValueError("max_active must be positive")
        self._slots = asyncio.Semaphore(max_active)
        self._resources: dict[Hashable, asyncio.Lock] = {}
        self.calls = 0

    def resource_lock(self, resource: Hashable) -> asyncio.Lock:
        if resource not in self._resources:
            self._resources[resource] = asyncio.Lock()
        return self._resources[resource]

    async def run(
        self, drive: Callable[[Any], Awaitable[XEvent]], request: Any
    ) -> XEvent:
        self.calls += 1
        try:
            async with self._slots:
                event = await drive(request)
                if not isinstance(event, XEvent):
                    raise TypeError("driver must return an XEvent at acceptance")
                return event
        finally:
            self.calls -= 1
