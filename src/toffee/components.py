from __future__ import annotations

from collections.abc import Awaitable, Iterable
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from ._context import current_reactor
from .events import XEvent
from .interfaces import Transfer
from .signals import as_xdata, signal_identity

T = TypeVar("T")


class Driver(ABC, Generic[T]):
    """Minimal contract for an active transaction source."""

    @abstractmethod
    def send(self, transaction: T) -> Awaitable[XEvent]:
        """Return an awaitable for the driver-defined input completion event.

        Protocol drivers normally return the input-acceptance event. A clocked
        level writer may instead return the event after its requested hold
        interval; callers must use that concrete driver's documented contract.
        Concrete drivers may return a coroutine or an already-submitted handle;
        the base contract does not require background execution or a queue.
        """

    @abstractmethod
    def close(self) -> None:
        """Release signal ownership held by this driver."""


class SignalDriver(Driver[T], Generic[T]):
    """Driver base that owns selected signal leaves, independent of their layout.

    Subclasses supply only the signals they drive.  Scheduling, encoding and
    transaction policy remain the responsibility of each concrete driver.
    """

    def __init__(self, signals: Iterable[Any], *, name: str) -> None:
        self.name = name
        self._driven_signals = tuple(as_xdata(signal) for signal in signals)
        if not self._driven_signals:
            raise ValueError(f"{name} driver requires at least one driven signal")
        self._reactor: Any = None
        self._claimed = False

    def _claim(self) -> None:
        reactor = current_reactor()
        if self._claimed:
            if reactor is not self._reactor:
                raise RuntimeError("driver is already bound to another Execution")
            return
        if any(
            callable(getattr(signal, "IsOutIO", None)) and signal.IsOutIO()
            for signal in self._driven_signals
        ):
            raise ValueError(f"{self.name} driver cannot drive DUT outputs")
        reactor.claim_driver(
            self,
            tuple(
                (signal_identity(signal), signal)
                for signal in self._driven_signals
            ),
        )
        self._reactor = reactor
        self._claimed = True

    def close(self) -> None:
        if self._claimed and self._reactor is not None:
            self._reactor.release_driver(self)
        self._reactor = None
        self._claimed = False

    async def __aenter__(self) -> "SignalDriver[T]":
        self._claim()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


class Monitor(ABC, Generic[T]):
    """Minimal contract for a passive transaction observer."""

    @abstractmethod
    def start(self, execution: Any) -> "Monitor[T]":
        """Attach this monitor to an active Execution."""

    @abstractmethod
    async def recv(self) -> Transfer[T]:
        """Receive the next immutable observed transfer."""

    @abstractmethod
    async def aclose(self) -> None:
        """Detach the monitor and cancel its internal registrations."""
