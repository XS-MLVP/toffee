from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .reactor import Reactor

_current_reactor: ContextVar["Reactor | None"] = ContextVar(
    "toffee_current_reactor", default=None
)


def current_reactor() -> "Reactor":
    reactor = _current_reactor.get()
    if reactor is None:
        raise RuntimeError("XTrigger must be awaited inside an active Execution")
    return reactor


def bind_reactor(reactor: "Reactor | None") -> Token["Reactor | None"]:
    return _current_reactor.set(reactor)


def reset_reactor(token: Token["Reactor | None"]) -> None:
    _current_reactor.reset(token)
