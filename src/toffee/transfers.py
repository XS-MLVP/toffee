"""Awaitable submissions completed by input acceptance or a later response."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import Enum
from itertools import count
from typing import Any, Generic, TypeVar

from .events import XEvent


RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")
_sequence_ids = count()


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()
"""Sentinel distinguishing an absent expected value from ``None``."""


class TransferState(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class XTransfer(Generic[RequestT, ResponseT]):
    """A submitted request with an explicit completion owner.

    ``XTransfer`` is a transaction handle, not a monitor observation. A driver owns
    the ``PENDING -> PROCESSING`` transition. For response transfers, the
    response owner completes or fails the handle. For ``AsyncDriver.send``, the
    driver completes it immediately at acceptance, with the same XEvent.
    """

    __slots__ = (
        "sequence_id",
        "request",
        "state",
        "accepted_event",
        "completed_event",
        "expected",
        "error",
        "_processing",
        "_done",
        "_callbacks",
        "_failure_observers",
        "_failure_observed",
    )

    def __init__(self, request: RequestT) -> None:
        loop = asyncio.get_running_loop()
        self.sequence_id = next(_sequence_ids)
        self.request = request
        self.state = TransferState.PENDING
        self.accepted_event: XEvent | None = None
        self.completed_event: XEvent | None = None
        self.expected: Any = MISSING
        self.error: BaseException | None = None
        self._processing: asyncio.Future[XEvent] = loop.create_future()
        self._done: asyncio.Future[ResponseT] = loop.create_future()
        self._callbacks: list[Callable[[XTransfer[RequestT, ResponseT]], None]] = []
        self._failure_observers: list[
            Callable[[XTransfer[RequestT, ResponseT], BaseException], None]
        ] = []
        self._failure_observed = False

    def set_expected(self, expected: Any) -> None:
        if self.state not in {TransferState.PENDING, TransferState.PROCESSING}:
            raise RuntimeError(
                f"transfer {self.sequence_id} expected value must be set "
                f"before completion, not {self.state.value}"
            )
        if self.expected is not MISSING:
            raise RuntimeError(
                f"transfer {self.sequence_id} already has an expected value"
            )
        self.expected = expected

    def mark_processing(self, event: XEvent) -> None:
        if self.state is not TransferState.PENDING:
            raise RuntimeError(
                f"transfer {self.sequence_id} cannot enter PROCESSING "
                f"from {self.state.value}"
            )
        self.state = TransferState.PROCESSING
        self.accepted_event = event
        self._processing.set_result(event)

    def complete(self, event: XEvent, response: ResponseT) -> None:
        if self.state is not TransferState.PROCESSING:
            raise RuntimeError(
                f"transfer {self.sequence_id} cannot complete "
                f"from {self.state.value}"
            )
        self.state = TransferState.COMPLETED
        self.completed_event = event
        self._done.set_result(response)
        self._notify_done()

    def cancel(self) -> None:
        if self.done():
            return
        if self.state is TransferState.PROCESSING:
            raise RuntimeError(
                f"transfer {self.sequence_id} is already accepted; "
                "cancelling a waiter does not cancel the transaction"
            )
        self.state = TransferState.CANCELLED
        if not self._processing.done():
            self._processing.cancel()
        self._done.cancel()
        self._notify_done()

    def fail(self, error: BaseException) -> None:
        if self.done():
            return
        self.state = TransferState.FAILED
        self.error = error
        if not self._processing.done():
            self._processing.set_exception(error)
            # Most callers await the final response rather than acceptance.
            self._processing.exception()
        self._done.set_exception(error)
        # Owners report failures through their lifecycle boundary. Retrieving
        # the exception here prevents GC warnings, without acknowledging it.
        self._done.exception()
        self._notify_done()

    def add_done_callback(
        self,
        callback: Callable[["XTransfer[RequestT, ResponseT]"], None],
    ) -> None:
        if self.done():
            callback(self)
        else:
            self._callbacks.append(callback)

    def add_failure_observer(
        self,
        callback: Callable[
            ["XTransfer[RequestT, ResponseT]", BaseException], None
        ],
    ) -> None:
        self._failure_observers.append(callback)
        if self._failure_observed and self.error is not None:
            callback(self, self.error)

    def observe_failure(self) -> None:
        """Acknowledge a failed transfer and suppress duplicate reporting."""

        if self.state is not TransferState.FAILED or self.error is None:
            return
        if self._failure_observed:
            return
        self._failure_observed = True
        # Retrieving the exception prevents asyncio's unobserved-Future warning.
        self._done.exception()
        for callback in tuple(self._failure_observers):
            callback(self, self.error)
        self._failure_observers.clear()

    @property
    def failure_observed(self) -> bool:
        return self._failure_observed

    def _notify_done(self) -> None:
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback(self)
        if self.state is not TransferState.FAILED:
            self._failure_observers.clear()

    async def wait_processing(self) -> XEvent:
        try:
            return await asyncio.shield(self._processing)
        except asyncio.CancelledError:
            raise
        except BaseException:
            self.observe_failure()
            raise

    def done(self) -> bool:
        return self.state in {
            TransferState.COMPLETED,
            TransferState.CANCELLED,
            TransferState.FAILED,
        }

    async def _wait(self) -> ResponseT:
        try:
            return await asyncio.shield(self._done)
        except asyncio.CancelledError:
            raise
        except BaseException:
            self.observe_failure()
            raise

    def __await__(self):
        return self._wait().__await__()

    def __repr__(self) -> str:
        return (
            f"XTransfer(sequence_id={self.sequence_id}, "
            f"state={self.state.value}, request={self.request!r})"
        )


__all__ = ["MISSING", "TransferState", "XTransfer"]
