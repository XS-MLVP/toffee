"""Private, runner-owned capture. Ordinary library use has no global collector."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from weakref import WeakSet

_sink: ContextVar[Any] = ContextVar("toffee_coverage_capture", default=None)
_suspended: ContextVar[bool] = ContextVar("toffee_coverage_capture_suspended", default=False)
_live: WeakSet[Any] = WeakSet()


def register(group: Any) -> None:
    if _suspended.get():
        return
    _live.add(group)
    sink = _sink.get()
    if sink is not None:
        sink.register(group)


def live_groups():
    return tuple(_live)


@contextmanager
def capture(sink: Any):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


@contextmanager
def suspended():
    token = _suspended.set(True)
    try:
        with capture(None):
            yield
    finally:
        _suspended.reset(token)
