from __future__ import annotations

import asyncio
import inspect
from collections.abc import Hashable
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from .backend import BackendHandle, BackendHit, RunResult, SimulationBackend
from .events import (
    ConditionEvent,
    EdgeEvent,
    FsmEvent,
    PhaseEvent,
    XEvent,
    XEventKind,
)
from .triggers import (
    CompiledTrigger,
    DriveStable,
    PhaseTrigger,
    PythonPredicateTrigger,
    Value,
    ValueChange,
    XTrigger,
)
from .subscriptions import BoundSubscriptionSpec


class SubscriptionOverflowError(RuntimeError):
    pass


@dataclass(slots=True)
class Registration:
    registration_id: int
    trigger: XTrigger[Any]
    future: asyncio.Future[XEvent]
    handle: BackendHandle
    active: bool = True


@dataclass(slots=True)
class Subscription:
    subscription_id: int
    trigger: XTrigger[Any]
    handle: BackendHandle
    queue: asyncio.Queue[Any]
    handler: Callable[[Any], Awaitable[None]]
    delivery: str
    capture: Callable[[XEvent], Any] | None = None
    task: asyncio.Task[None] | None = None
    active: bool = True
    # An owning component receives terminal state instead of the global pump.
    # Ordinary @on subscriptions leave this unset and retain Execution errors.
    _on_stop: Callable[[BaseException | None], None] | None = None


class Reactor:
    def __init__(
        self,
        backend: SimulationBackend,
        *,
        default_sample: PhaseTrigger | None = None,
    ) -> None:
        self.backend = backend
        self.default_sample = default_sample
        self.work_available = asyncio.Event()
        self._next_registration_id = 1
        self._registrations: dict[tuple[int, int], Registration] = {}
        self._subscriptions: dict[tuple[int, int], Subscription] = {}
        self._next_subscription_id = 1
        self._background_error: BaseException | None = None
        self._drive_owners: dict[Hashable, tuple[object, Any]] = {}
        self._monitor_consumers: dict[int, tuple[object, object]] = {}
        self._closed = False

    def register(self, trigger: XTrigger[Any]) -> Registration:
        if self._closed:
            raise RuntimeError("Reactor is closed")
        trigger = self._resolve_sample(trigger)
        self._check_phase_support(trigger)
        loop = asyncio.get_running_loop()
        handle = self.backend.arm(trigger)
        registration = Registration(
            self._next_registration_id,
            trigger,
            loop.create_future(),
            handle,
        )
        self._next_registration_id += 1
        key = (handle.slot, handle.generation)
        self._registrations[key] = registration
        registration.future.add_done_callback(
            lambda future, reg=registration: self.cancel(reg)
            if future.cancelled()
            else None
        )
        self.work_available.set()
        return registration

    def subscribe(self, spec: BoundSubscriptionSpec) -> Subscription:
        if self._closed:
            raise RuntimeError("Reactor is closed")
        trigger = self._resolve_sample(spec.trigger)
        self._check_phase_support(trigger)
        handle = self.backend.arm(trigger)
        queue: asyncio.Queue[Any] = asyncio.Queue(spec.capacity)
        subscription = Subscription(
            self._next_subscription_id,
            trigger,
            handle,
            queue,
            spec.handler,
            spec.delivery,
            spec.capture,
        )
        self._next_subscription_id += 1
        subscription.task = asyncio.create_task(
            self._run_subscription(subscription),
            name=f"toffee-subscription-{subscription.subscription_id}",
        )
        subscription.task.add_done_callback(
            lambda task, sub=subscription: self._subscription_done(sub, task)
        )
        self._subscriptions[(handle.slot, handle.generation)] = subscription
        self.work_available.set()
        return subscription

    def _resolve_sample(self, trigger: XTrigger[Any]) -> XTrigger[Any]:
        if not isinstance(
            trigger,
            (Value, ValueChange, CompiledTrigger, PythonPredicateTrigger),
        ):
            return trigger
        if trigger.sample is not None:
            return trigger
        if self.default_sample is None:
            raise ValueError(
                f"{type(trigger).__name__} requires sample= or "
                "Execution(default_sample=...)"
            )
        return replace(trigger, sample=self.default_sample)

    def _check_phase_support(self, trigger: XTrigger[Any]) -> None:
        if _uses_drive_stable(trigger) and not getattr(
            self.backend.capabilities, "drive_stable", False
        ):
            raise RuntimeError(
                "backend does not support the DriveStable sampling phase"
            )

    def cancel(self, registration: Registration) -> None:
        if not registration.active:
            return
        registration.active = False
        key = (registration.handle.slot, registration.handle.generation)
        self._registrations.pop(key, None)
        self.backend.disarm(registration.handle)
        if not self._registrations and not self._subscriptions:
            self.work_available.clear()

    def publish(self, result: RunResult) -> tuple[XEvent, ...]:
        event_cache: dict[int, XEvent] = {}
        delivered: list[XEvent] = []
        for hit in result.hits:
            key = (hit.slot, hit.generation)
            subscription = self._subscriptions.get(key)
            if subscription is not None and subscription.active:
                event = event_cache.get(hit.event_id)
                if event is None:
                    event = _event_from_hit(hit)
                    event_cache[hit.event_id] = event
                    delivered.append(event)
                try:
                    item: Any = event
                    if subscription.capture is not None:
                        item = subscription.capture(event)
                        if inspect.isawaitable(item):
                            close = getattr(item, "close", None)
                            if close is not None:
                                close()
                            raise TypeError(
                                "subscription capture must be synchronous"
                            )
                    if not self.backend.rearm(subscription.handle):
                        raise RuntimeError("backend failed to rearm subscription")
                    self._enqueue(subscription, item)
                except Exception as error:
                    if subscription._on_stop is None:
                        raise
                    self._stop_subscription(subscription, error)
                continue
            registration = self._registrations.get(key)
            if registration is None or not registration.active:
                continue
            event = event_cache.get(hit.event_id)
            if event is None:
                event = _event_from_hit(hit)
                event_cache[hit.event_id] = event
                delivered.append(event)
            registration.active = False
            self._registrations.pop(key, None)
            self.backend.disarm(registration.handle)
            if not registration.future.done():
                registration.future.set_result(event)
        if not self._registrations and not self._subscriptions:
            self.work_available.clear()
        return tuple(delivered)

    def fail(self, error: BaseException) -> None:
        for registration in tuple(self._registrations.values()):
            registration.active = False
            self.backend.disarm(registration.handle)
            if not registration.future.done():
                registration.future.set_exception(error)
        self._registrations.clear()
        for subscription in tuple(self._subscriptions.values()):
            self._stop_subscription(subscription, error)
        self.work_available.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for registration in tuple(self._registrations.values()):
            registration.active = False
            self.backend.disarm(registration.handle)
            if not registration.future.done():
                registration.future.cancel()
        self._registrations.clear()
        for subscription in tuple(self._subscriptions.values()):
            self.cancel_subscription(subscription)
        self._drive_owners.clear()
        self._monitor_consumers.clear()
        self.work_available.set()

    def _claim_monitor_consumer(self, monitor: object, owner: object) -> None:
        previous = self._monitor_consumers.get(id(monitor))
        if previous is not None and previous[1] is not owner:
            raise RuntimeError("monitor already has a Scoreboard consumer")
        self._monitor_consumers[id(monitor)] = (monitor, owner)

    def _release_monitor_consumer(self, monitor: object, owner: object) -> None:
        previous = self._monitor_consumers.get(id(monitor))
        if previous is not None and previous[1] is owner:
            self._monitor_consumers.pop(id(monitor))

    def claim_driver(
        self, owner: object, signals: tuple[tuple[Hashable, Any], ...]
    ) -> None:
        conflicts = [
            signal
            for identity, signal in signals
            if identity in self._drive_owners
            and self._drive_owners[identity][0] is not owner
        ]
        if conflicts:
            names = ", ".join(
                str(getattr(signal, "mName", repr(signal)))
                for signal in conflicts
            )
            raise RuntimeError(f"signals already have a driver: {names}")
        for identity, signal in signals:
            self._drive_owners[identity] = (owner, signal)

    def release_driver(self, owner: object) -> None:
        for identity, (current, _) in tuple(self._drive_owners.items()):
            if current is owner:
                del self._drive_owners[identity]

    async def aclose(self) -> None:
        tasks = tuple(
            subscription.task
            for subscription in self._subscriptions.values()
            if subscription.task is not None
        )
        self.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def has_execution_work(self) -> bool:
        return bool(self._registrations or self._subscriptions)

    @property
    def has_drive_stable_work(self) -> bool:
        return any(
            _uses_drive_stable(item.trigger)
            for item in (
                *self._registrations.values(),
                *self._subscriptions.values(),
            )
        )

    def cancel_subscription(self, subscription: Subscription) -> None:
        self._stop_subscription(subscription)

    def _stop_subscription(
        self, subscription: Subscription, error: BaseException | None = None
    ) -> None:
        if not subscription.active:
            return
        task = subscription.task
        if error is None and task is not None and task.done() and not task.cancelled():
            error = task.exception()
        subscription.active = False
        key = (subscription.handle.slot, subscription.handle.generation)
        self._subscriptions.pop(key, None)
        try:
            self.backend.disarm(subscription.handle)
        finally:
            if subscription.task is not None and not subscription.task.done():
                subscription.task.cancel()
            if subscription._on_stop is not None:
                subscription._on_stop(error)
            if not self._registrations and not self._subscriptions:
                self.work_available.clear()

    def raise_background_error(self) -> None:
        if self._background_error is not None:
            error = self._background_error
            self._background_error = None
            raise error

    def _enqueue(self, subscription: Subscription, item: Any) -> None:
        if subscription.delivery == "latest" and subscription.queue.full():
            subscription.queue.get_nowait()
            subscription.queue.task_done()
        try:
            subscription.queue.put_nowait(item)
        except asyncio.QueueFull as error:
            raise SubscriptionOverflowError(
                f"subscription {subscription.subscription_id} queue overflow "
                f"(capacity={subscription.queue.maxsize})"
            ) from error

    async def _run_subscription(self, subscription: Subscription) -> None:
        while subscription.active:
            item = await subscription.queue.get()
            try:
                await subscription.handler(item)
            finally:
                subscription.queue.task_done()

    def _subscription_done(
        self, subscription: Subscription, task: asyncio.Task[None]
    ) -> None:
        if task.cancelled():
            self.cancel_subscription(subscription)
            return
        error = task.exception()
        if error is not None:
            if subscription._on_stop is not None:
                if subscription.active:
                    self._stop_subscription(subscription, error)
                else:
                    # A handler can fail while unwinding cancellation after
                    # its subscription has already been stopped.
                    subscription._on_stop(error)
            elif not self._closed:
                self._background_error = error
                self.cancel_subscription(subscription)
                self.work_available.set()


def _event_from_hit(hit: BackendHit) -> XEvent:
    fields = dict(
        event_id=hit.event_id,
        tick=hit.tick,
        phase=hit.phase,
        kind=hit.kind,
        source=hit.source,
        value=hit.value,
    )
    if hit.kind in (XEventKind.CLOCK_RISE, XEventKind.CLOCK_FALL):
        return EdgeEvent(**fields)
    if hit.kind is XEventKind.DRIVE_STABLE:
        return PhaseEvent(**fields)
    if hit.kind is XEventKind.FSM:
        return FsmEvent(
            **fields,
            terminal_state=hit.terminal_state or "MATCHED",
        )
    return ConditionEvent(**fields)


def _uses_drive_stable(trigger: XTrigger[Any]) -> bool:
    return isinstance(trigger, DriveStable) or isinstance(
        getattr(trigger, "sample", None), DriveStable
    )
