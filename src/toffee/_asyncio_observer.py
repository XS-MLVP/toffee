"""Observe native asyncio callbacks; never select or execute tasks ourselves."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any


current_domain: ContextVar[CallbackDomain | None] = ContextVar(
    "toffee_callback_domain", default=None,
)
_observers: dict[asyncio.AbstractEventLoop, _LoopObserver] = {}
_MISSING = object()


class SimulationNotSettledError(RuntimeError):
    """Immediate simulation work exceeded the configured settling budget."""


class _PendingCall:
    __slots__ = ("domain", "callback", "handle")

    def __init__(self, domain: CallbackDomain, callback: Any) -> None:
        self.domain = domain
        self.callback = callback
        self.handle: asyncio.Handle | None = None

    def __call__(self, *args: Any) -> None:
        try:
            self.callback(*args)
        finally:
            self.domain.pending.discard(self)
            # Break the Handle -> callback -> Handle cycle on the hot path.
            self.handle = None

    def __repr__(self) -> str:
        return f"observed({self.callback!r})"


class _LoopObserver:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        if not isinstance(loop, asyncio.BaseEventLoop):
            raise RuntimeError(
                "Execution callback observation requires an asyncio.BaseEventLoop; "
                f"unsupported loop: {type(loop).__module__}.{type(loop).__name__}"
            )
        self.loop = loop
        self.original = original = loop.call_soon
        self.original_attribute = vars(loop).get("call_soon", _MISSING)
        self.users: set[CallbackDomain] = set()

        def call_soon(callback, *args, context=None):
            domain = current_domain.get() if context is None else context.get(current_domain)
            if domain is None or domain.closed or domain.observer is not self:
                # Task yields commonly have no arguments. Avoid allocating an
                # unpacked keyword call on every unrelated host continuation.
                if not args:
                    return original(callback, context=context)
                return original(callback, *args, context=context)
            # Let the original entry validate invalid callbacks, including its
            # debug-only coroutine check. Wrapping must not hide those errors.
            if not callable(callback) or (
                loop.get_debug()
                and (asyncio.iscoroutine(callback) or asyncio.iscoroutinefunction(callback))
            ):
                return original(callback, *args, context=context)
            call = _PendingCall(domain, callback)
            domain.pending.add(call)
            try:
                if not args:
                    handle = original(call, context=context)
                elif len(args) == 1:
                    handle = original(call, args[0], context=context)
                else:
                    handle = original(call, *args, context=context)
            except BaseException:
                domain.pending.discard(call)
                raise
            call.handle = handle
            return handle

        self.hook = call_soon
        try:
            loop.call_soon = call_soon
        except (AttributeError, TypeError) as error:
            raise RuntimeError("event loop does not support callback observation") from error

    @classmethod
    def attach(cls, domain: CallbackDomain) -> _LoopObserver:
        loop = asyncio.get_running_loop()
        observer = _observers.get(loop)
        if observer is None:
            observer = cls(loop)
            _observers[loop] = observer
        else:
            observer.check()
        observer.users.add(domain)
        return observer

    def check(self) -> None:
        if self.loop.call_soon is not self.hook:
            raise RuntimeError(
                "loop.call_soon changed during Execution; "
                "callback settlement can no longer be guaranteed"
            )

    def detach(self, domain: CallbackDomain) -> None:
        self.users.discard(domain)
        if self.users:
            return
        # A host-installed replacement belongs to the host, even if it wraps us.
        if self.loop.call_soon is self.hook:
            if self.original_attribute is _MISSING:
                delattr(self.loop, "call_soon")
            else:
                self.loop.call_soon = self.original_attribute
        _observers.pop(self.loop, None)


class CallbackDomain:
    def __init__(self, max_settle_rounds: int) -> None:
        self.pending: set[_PendingCall] = set()
        self.closed = False
        self.max_settle_rounds = max_settle_rounds
        self._detached = False
        self.observer = _LoopObserver.attach(self)

    async def settle(self, backend: Any) -> None:
        self.observer.check()
        rounds = 0
        while self.pending and not self.closed:
            # Only our registered Handles are inspected, never the loop queue
            # or Task internals. A cancelled Handle won't call our finally.
            for call in tuple(self.pending):
                if call.handle is not None and call.handle.cancelled():
                    self.pending.discard(call)
                    call.handle = None
                    call.callback = None
            if not self.pending:
                return
            if rounds >= self.max_settle_rounds:
                callbacks = ", ".join(repr(call) for call in list(self.pending)[:3])
                phase = getattr(backend, "phase", None)
                raise SimulationNotSettledError(
                    f"simulation did not settle at tick={getattr(backend, 'tick', '?')} "
                    f"phase={getattr(phase, 'name', phase)} after {rounds} rounds; "
                    f"{len(self.pending)} pending callbacks: {callbacks}"
                )
            await asyncio.sleep(0)
            rounds += 1
            self.observer.check()

    def stop(self) -> None:
        self.closed = True
        # Scheduled callbacks still run normally, with their original owner.
        self.pending.clear()

    def close(self) -> None:
        if self._detached:
            return
        self.stop()
        self.observer.detach(self)
        self._detached = True
