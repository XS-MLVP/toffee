from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .events import XEvent
from .triggers import XTrigger

Handler = Callable[[Any], Awaitable[None]]
Capture = Callable[[XEvent], Any]


@dataclass(frozen=True, slots=True)
class BoundSubscriptionSpec:
    trigger: XTrigger[Any]
    handler: Handler
    delivery: str
    capacity: int
    capture: Capture | None = None


@dataclass(frozen=True, slots=True)
class XSubscriptionSpec:
    trigger_source: Any
    handler: Handler
    delivery: str = "lossless"
    capacity: int = 64
    capture: Capture | None = None

    def bind(self, *args: Any, **kwargs: Any) -> BoundSubscriptionSpec:
        source = self.trigger_source
        trigger = source(*args, **kwargs) if callable(source) else source
        if not isinstance(trigger, XTrigger):
            raise TypeError("@on source must produce an XTrigger")
        return BoundSubscriptionSpec(
            trigger, self.handler, self.delivery, self.capacity, self.capture
        )


def on(
    trigger: Any,
    *,
    delivery: str = "lossless",
    capacity: int = 64,
    capture: Capture | None = None,
) -> Callable[[Handler], XSubscriptionSpec]:
    if delivery not in ("lossless", "latest"):
        raise ValueError("delivery must be 'lossless' or 'latest'")
    if capacity <= 0:
        raise ValueError("subscription capacity must be positive")
    if capture is not None and not callable(capture):
        raise TypeError("subscription capture must be callable")
    if capture is not None and inspect.iscoroutinefunction(capture):
        raise TypeError("subscription capture must be synchronous")

    def decorate(handler: Handler) -> XSubscriptionSpec:
        if not inspect.iscoroutinefunction(handler):
            raise TypeError("@on handler must be async def")
        return XSubscriptionSpec(
            trigger, handler, delivery, capacity, capture
        )

    return decorate
