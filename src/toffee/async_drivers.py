"""Asynchronous transaction submission with configurable input-side overlap."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections import deque
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .components import SignalDriver
from ._driver_runtime import DriveControl
from ._cleanup import raise_cleanup_errors
from ._context import current_reactor
from .data import DataNode, drive_data, iter_data_leaves, normalize_data
from .events import XEvent
from .signals import as_xdata
from .transfers import TransferState, XTransfer
from .triggers import ClockCycles


RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


@dataclass(frozen=True, slots=True)
class _DriverError:
    error: BaseException


class DriverIncompleteError(RuntimeError):
    """An accepted request was still awaiting completion when its driver closed."""


class AsyncDriver(SignalDriver[RequestT], Generic[RequestT, ResponseT]):
    """Submit input transactions without waiting for acceptance or response.

    A transaction may take one or many cycles to drive. ``max_active=1``
    serializes complete input transactions; larger values allow their stages
    to overlap. A subclass must arbitrate any shared signal/resource, using
    ``resource_lock`` if appropriate. ``send`` completes at acceptance;
    ``submit`` leaves response completion to a monitor/scoreboard.
    """

    def __init__(
        self,
        signals: Iterable[Any],
        *,
        name: str,
        capacity: int = 64,
        max_active: int = 1,
    ) -> None:
        super().__init__(signals, name=name)
        if capacity <= 0:
            raise ValueError("driver capacity must be positive")
        self.capacity = capacity
        self.max_active = max_active
        self._control = DriveControl(max_active)
        self._accepted: asyncio.Queue[XTransfer[RequestT, ResponseT] | _DriverError] = (
            asyncio.Queue(capacity)
        )
        # Both response transfers and input-only XEvent results share the same
        # capacity, queue, workers and failure/cleanup ownership.
        self._outstanding: dict[int, XTransfer[RequestT, Any]] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._pending: deque[XTransfer[RequestT, Any]] = deque()
        self._worker: asyncio.Task[None] | None = None
        self._started = False
        self._failure: BaseException | None = None
        self._failure_observed = False
        self._closing = False
        self._serial_current: XTransfer[RequestT, Any] | None = None
        self._accept_only: set[int] = set()
        self._cleanup_errors: list[BaseException] = []

    def start(self, execution: Any) -> AsyncDriver[RequestT, ResponseT]:
        if execution.reactor is None or execution.reactor is not current_reactor():
            raise RuntimeError("async driver requires an active Execution")
        self._activate()
        return self

    def _activate(self) -> None:
        if self._started:
            raise RuntimeError("async driver is already started")
        if self._failure is not None:
            raise RuntimeError("failed async driver cannot be restarted") from self._failure
        self._claim()
        self._closing = False
        self._started = True

    async def __aenter__(self) -> AsyncDriver[RequestT, ResponseT]:
        self._activate()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self._close_async(primary=exc)

    def submit(self, request: RequestT) -> XTransfer[RequestT, ResponseT]:
        """Submit a request whose response owner must complete the transfer."""

        return self._submit(request, accept_only=False)

    def send(self, transaction: RequestT) -> XTransfer[RequestT, XEvent]:
        """Submit input now; awaiting its handle returns the acceptance event.

        Calling this method queues input immediately, even without an await.
        Acceptance completes the handle and releases its submission capacity.
        No response or accepted-stream consumer is needed. Cancelling a waiter
        leaves the input running; ``handle.cancel()`` withdraws pending input.
        The driver's context owns the work even when nobody awaits the handle.
        """

        return self._submit(transaction, accept_only=True)

    def _submit(self, request: RequestT, *, accept_only: bool) -> XTransfer[RequestT, Any]:
        if not self._started:
            raise RuntimeError("async driver is not started")
        if self._failure is not None:
            raise RuntimeError("async driver has failed") from self._failure
        if len(self._outstanding) >= self.capacity:
            raise RuntimeError(f"{self.name} submission capacity is full")
        transfer: XTransfer[RequestT, Any] = XTransfer(request)
        if accept_only:
            self._accept_only.add(transfer.sequence_id)
        self._outstanding[transfer.sequence_id] = transfer
        transfer.add_done_callback(self.retire)
        transfer.add_failure_observer(self._on_failure_observed)
        if self.max_active == 1:
            self._pending.append(transfer)
            if self._worker is None or self._worker.done():
                self._worker = asyncio.create_task(
                    self._run_serial(), name=f"{self.name}-drive-serial"
                )
        else:
            task = asyncio.create_task(
                self._run_one(transfer), name=f"{self.name}-drive-{transfer.sequence_id}"
            )
            self._tasks[transfer.sequence_id] = task
            task.add_done_callback(
                lambda done, key=transfer.sequence_id: self._tasks.pop(key, None)
            )
        return transfer

    async def recv_accepted(self) -> XTransfer[RequestT, ResponseT]:
        while True:
            if self._failure is not None:
                self._failure_observed = True
                raise self._failure
            item = await self._accepted.get()
            self._accepted.task_done()
            if isinstance(item, _DriverError):
                self._failure_observed = True
                raise item.error
            if item.state is TransferState.PROCESSING:
                return item

    def retire(self, transfer: XTransfer[RequestT, Any]) -> None:
        self._outstanding.pop(transfer.sequence_id, None)
        self._accept_only.discard(transfer.sequence_id)
        if transfer.state is TransferState.FAILED and self._failure is None:
            self._failure = transfer.error
        if transfer in self._pending:
            self._pending.remove(transfer)
        task = self._tasks.get(transfer.sequence_id)
        if self._serial_current is transfer:
            task = self._worker
        # A terminal response does not revoke an already-accepted input.
        if (
            transfer.accepted_event is None
            and task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()

    @property
    def has_outstanding(self) -> bool:
        return bool(self._outstanding)

    def quiesce(self) -> None:
        """Restore protocol idle state, if needed; subclasses may override."""

    def resource_lock(self, resource: Hashable) -> asyncio.Lock:
        """Return the shared native asyncio lock for this resource."""

        return self._control.resource_lock(resource)

    @abstractmethod
    async def _drive_one(self, request: RequestT) -> XEvent:
        """Drive one complete input transaction and return its acceptance event."""

    async def _run_serial(self) -> None:
        # Reuse the worker to avoid per-transfer task allocation for adjacent
        # submissions. Execution handles phase ordering for all native tasks.
        while self._pending and self._failure is None:
            transfer = self._pending.popleft()
            if not transfer.done():
                self._serial_current = transfer
                try:
                    await self._run_one(transfer)
                except asyncio.CancelledError:
                    if self._closing or transfer.state is not TransferState.CANCELLED:
                        raise
                    # Explicitly withdrawing this unaccepted input must not
                    # discard later inputs queued on the same serial worker.
                    asyncio.current_task().uncancel()
                finally:
                    self._serial_current = None

    async def _run_one(self, transfer: XTransfer[RequestT, Any]) -> None:
        try:
            if transfer.done():
                return
            event = await self._control.run(self._drive_one, transfer.request)
            if not transfer.done():
                accept_only = transfer.sequence_id in self._accept_only
                if not accept_only and self._accepted.full():
                    raise RuntimeError(f"{self.name} accepted stream is full")
                transfer.mark_processing(event)
                if accept_only:
                    transfer.complete(event, event)
                else:
                    self._accepted.put_nowait(transfer)
        except asyncio.CancelledError:
            if not transfer.done():
                transfer.cancel()
            raise
        except BaseException as error:
            if self._closing:
                self._cleanup_errors.append(error)
            else:
                self._fail(error)
        finally:
            self._tasks.pop(transfer.sequence_id, None)

    def close(self) -> None:
        if (
            (self._worker is not None and not self._worker.done())
            or any(not task.done() for task in self._tasks.values())
        ):
            raise RuntimeError("async driver has active input work; await aclose()")
        errors = self._terminate_transfers()
        self._release()
        raise_cleanup_errors(f"{self.name}: close failures", errors)

    async def aclose(self) -> None:
        await self._close_async()

    async def _close_async(self, primary: BaseException | None = None) -> None:
        self._closing = True
        tasks = tuple(self._tasks.values())
        worker = self._worker
        self._started = False
        errors = self._terminate_transfers(primary)
        for task in tasks:
            task.cancel()
        if worker is not None and not worker.done():
            worker.cancel()
            tasks = (*tasks, worker)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        errors.extend(self._cleanup_errors)
        self._cleanup_errors.clear()
        self._worker = None
        self._release()
        raise_cleanup_errors(f"{self.name}: body and cleanup failures", errors, primary)

    def _release(self) -> None:
        self._started = False
        self._pending.clear()
        self._tasks.clear()
        super().close()
        while not self._accepted.empty():
            self._accepted.get_nowait()
            self._accepted.task_done()

    def _terminate_transfers(self, primary: BaseException | None = None) -> list[BaseException]:
        errors: list[BaseException] = []
        accepted = [item for item in self._outstanding.values()
                    if item.state is TransferState.PROCESSING]
        reason = primary or self._failure
        if accepted and reason is None:
            reason = DriverIncompleteError(
                f"{self.name}: closing with {len(accepted)} incomplete accepted transfer(s)"
            )
            self._failure = reason
        if self._failure is not None and not self._failure_observed:
            errors.append(self._failure)
        for transfer in tuple(self._outstanding.values()):
            if transfer.state is TransferState.PENDING:
                transfer.cancel()
            elif transfer.state is TransferState.PROCESSING:
                assert reason is not None
                transfer.fail(reason)
        self._failure_observed = True
        return errors

    def _fail(self, error: BaseException) -> None:
        if self._failure is None:
            self._failure = error
            self._failure_observed = False
        for outstanding in tuple(self._outstanding.values()):
            outstanding.fail(self._failure)
        if not self._accepted.full():
            self._accepted.put_nowait(_DriverError(self._failure))

    def _on_failure_observed(
        self, transfer: XTransfer[RequestT, Any], error: BaseException
    ) -> None:
        if error is self._failure:
            self._failure_observed = True


class AsyncSingleCycleDriver(AsyncDriver[RequestT, ResponseT], Generic[RequestT, ResponseT]):
    """Drive one bound data node per clock without awaiting older responses.

    The data node may be a scalar signal, Bundle, packed view, or sequence.
    ``idle`` is the value to drive between requests and may depend on the
    previously accepted request. The driver owns its data leaves, not a
    particular Bundle shape or project-specific binding object.
    """

    def __init__(
        self,
        clock: Any,
        bits: DataNode,
        *,
        idle: Any,
        name: str = "single_cycle",
        encoder: Callable[[RequestT], Any] | None = None,
        capacity: int = 64,
    ) -> None:
        self.clock = as_xdata(clock)
        self.bits = normalize_data(bits, name)
        self.idle = idle
        self.encoder = encoder
        self._last_request: RequestT | None = None
        super().__init__(
            (signal for _, signal in iter_data_leaves(self.bits, name)),
            name=name,
            capacity=capacity,
            max_active=1,
        )

    def _idle_values(self) -> Any:
        return self.idle(self._last_request) if callable(self.idle) else self.idle

    def _drive_idle(self) -> None:
        drive_data(self.bits, self._idle_values(), self.name)

    def start(self, execution: Any) -> AsyncSingleCycleDriver[RequestT, ResponseT]:
        super().start(execution)
        try:
            self._last_request = None
            self._drive_idle()
        except BaseException:
            self.close()
            raise
        return self

    async def __aenter__(self) -> AsyncSingleCycleDriver[RequestT, ResponseT]:
        await super().__aenter__()
        try:
            self._last_request = None
            self._drive_idle()
        except BaseException:
            self.close()
            raise
        return self

    async def _drive_one(self, request: RequestT) -> XEvent:
        values = request if self.encoder is None else self.encoder(request)
        drive_data(self.bits, values, self.name)
        try:
            event = await ClockCycles(self.clock, 1)
            self._last_request = request
            return event
        finally:
            self._drive_idle()

    def quiesce(self) -> None:
        self._last_request = None
        self._drive_idle()
