from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Self, TypeVar

from .components import Monitor
from ._cleanup_errors import raise_cleanup_errors
from .data import sample_data
from .events import XEvent
from .interfaces import ReadyValid, Role, Transfer
from .reactor import Registration, Subscription, Reactor
from .execution import Execution
from .subscriptions import BoundSubscriptionSpec
from .triggers import RisingEdge, XTrigger

T = TypeVar("T")
Decoder = Callable[[Any], T]


class MonitorOverflowError(RuntimeError):
    pass


class MonitorClosedError(RuntimeError):
    """The monitor has terminated; no further observations can be received."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    value: Any
    rising: Registration


class _SubscriptionMonitor(Monitor[T], Generic[T]):
    """Shared delivery and ownership; subclasses define sampling semantics."""

    def __init__(
        self,
        *,
        delivery: str = "lossless",
        capacity: int = 64,
    ) -> None:
        if delivery not in ("lossless", "latest"):
            raise ValueError("delivery must be 'lossless' or 'latest'")
        if capacity <= 0:
            raise ValueError("monitor capacity must be positive")
        self.delivery = delivery
        self.capacity = capacity
        self._queue: asyncio.Queue[Transfer[T]] = asyncio.Queue(capacity)
        self._subscription: Subscription | None = None
        self._reactor: Reactor | None = None
        self._available = asyncio.Event()
        self._closed = False
        self._failure: BaseException | None = None
        self._failure_observed = False
        self._cleanup_errors: list[BaseException] = []

    def start(self, execution: Execution) -> Self:
        if self._closed:
            raise MonitorClosedError("monitor is closed; create a new instance")
        if self._subscription is not None:
            raise RuntimeError("monitor is already started")
        if execution.reactor is None:
            raise RuntimeError("monitor requires an active Execution")
        reactor = execution.reactor

        self._subscription = execution.subscribe(self._spec(reactor))
        self._reactor = reactor
        self._subscription._on_stop = self._stopped
        return self

    def _spec(self, reactor: Reactor) -> BoundSubscriptionSpec:
        raise NotImplementedError

    def _discard_pending(self) -> None:
        pass

    async def recv(self) -> Transfer[T]:
        while True:
            if self._failure is not None:
                self._failure_observed = True
                raise self._failure
            if self._closed:
                raise MonitorClosedError("monitor is closed")
            if self._subscription is None:
                raise RuntimeError("monitor is not started")
            if not self._queue.empty():
                transfer = self._queue.get_nowait()
                self._queue.task_done()
                return transfer
            self._available.clear()
            await self._available.wait()

    async def __aenter__(self) -> Self:
        if self._closed:
            raise MonitorClosedError("monitor is closed")
        if self._subscription is None:
            raise RuntimeError("monitor is not started")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            await self.aclose()
        except BaseException as error:
            raise_cleanup_errors("monitor body and cleanup failures", [error], exc)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Transfer[T]:
        try:
            return await self.recv()
        except MonitorClosedError:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        subscription = self._subscription
        try:
            if subscription is not None and self._reactor is not None:
                self._reactor.cancel_subscription(subscription)
            else:
                self._stopped(None)
        except BaseException as error:
            self._cleanup_errors.append(error)
        finally:
            if subscription is not None and subscription.task is not None:
                await asyncio.gather(subscription.task, return_exceptions=True)
                subscription._on_stop = None
            self._subscription = None
            self._reactor = None
        errors, self._cleanup_errors = self._cleanup_errors, []
        if self._failure is not None and not self._failure_observed:
            self._failure_observed = True
            errors.insert(0, self._failure)
        raise_cleanup_errors("monitor shutdown failures", errors)

    def _stopped(self, error: BaseException | None) -> None:
        self._closed = True
        if error is not None and self._failure is None:
            self._failure = error
        elif error is not None and error is not self._failure:
            self._cleanup_errors.append(error)
        self._available.set()
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._discard_pending()
        if self._subscription is not None:
            while not self._subscription.queue.empty():
                self._subscription.queue.get_nowait()
                self._subscription.queue.task_done()

    def _put(self, transfer: Transfer[T]) -> None:
        if self.delivery == "latest" and self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
        try:
            self._queue.put_nowait(transfer)
            self._available.set()
        except asyncio.QueueFull as error:
            raise MonitorOverflowError(
                f"monitor queue overflow (capacity={self.capacity})"
            ) from error


class SamplingMonitor(_SubscriptionMonitor[T]):
    """Capture a snapshot synchronously at each explicit trigger event.

    ``capture(event)`` must return a snapshot, not live mutable DUT state or an
    awaitable. Use Bundle.sample() or construct an immutable value. The returned
    Transfer keeps the trigger event unchanged; no acceptance phase is inferred.
    """

    def __init__(
        self,
        trigger: XTrigger[Any],
        *,
        capture: Callable[[XEvent], T],
        delivery: str = "lossless",
        capacity: int = 64,
    ) -> None:
        if not callable(capture) or inspect.iscoroutinefunction(capture):
            raise TypeError("monitor capture must be synchronous and callable")
        super().__init__(delivery=delivery, capacity=capacity)
        self.trigger = trigger
        self.capture = capture

    def _spec(self, reactor: Reactor) -> BoundSubscriptionSpec:
        def capture(event: XEvent) -> Transfer[T]:
            value = self.capture(event)
            if inspect.isawaitable(value):
                close = getattr(value, "close", None)
                if close is not None:
                    close()
                raise TypeError("monitor capture must be synchronous")
            return Transfer(event, value)

        async def deliver(observation: Transfer[T]) -> None:
            self._put(observation)

        return BoundSubscriptionSpec(
            trigger=self.trigger, capture=capture, handler=deliver,
            delivery=self.delivery, capacity=self.capacity,
        )


class ReadyValidMonitor(_SubscriptionMonitor[T]):
    def __init__(
        self,
        interface: ReadyValid,
        *,
        decoder: Decoder[T] | None = None,
        delivery: str = "lossless",
        capacity: int = 64,
    ) -> None:
        if interface.role is not Role.MONITOR:
            raise ValueError("ReadyValidMonitor requires role=MONITOR")
        super().__init__(delivery=delivery, capacity=capacity)
        self.interface = interface
        self.decoder = decoder
        self._candidates: dict[int, Registration] = {}

    def _spec(self, reactor: Reactor) -> BoundSubscriptionSpec:
        def capture(event: XEvent) -> _Candidate:
            value = sample_data(self.interface.bits)
            rising = reactor.register(RisingEdge(self.interface.clock))
            self._candidates[rising.registration_id] = rising
            return _Candidate(value, rising)

        async def deliver(candidate: _Candidate) -> None:
            try:
                event = await candidate.rising.future
            finally:
                self._candidates.pop(candidate.rising.registration_id, None)
                reactor.cancel(candidate.rising)
            value = candidate.value if self.decoder is None else self.decoder(candidate.value)
            self._put(Transfer(event=event, value=value))

        return BoundSubscriptionSpec(
            trigger=self.interface.fire, handler=deliver, capture=capture,
            delivery="lossless", capacity=self.capacity,
        )

    def _discard_pending(self) -> None:
        for registration in self._candidates.values():
            try:
                assert self._reactor is not None
                self._reactor.cancel(registration)
            except BaseException as cleanup_error:
                self._cleanup_errors.append(cleanup_error)
            finally:
                if not registration.future.done():
                    registration.future.cancel()
                elif not registration.future.cancelled():
                    registration.future.exception()
        self._candidates.clear()
