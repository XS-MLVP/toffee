"""An interface instance with driver, monitors and an optional model connection."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Hashable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Generic, Self, TypeVar

from ._context import current_reactor
from ._cleanup import raise_cleanup_errors
from .components import Monitor
from .events import XEvent
from .scoreboard import Scoreboard, ScoreboardStatus
from .sync_drivers import SyncDriver
from .transfers import MISSING, XTransfer

if TYPE_CHECKING:
    from .execution import Execution

DriverT = TypeVar("DriverT")


class _Acceptance:
    """Acceptance view of a response transfer; no task or second transaction."""

    def __init__(self, transfer: XTransfer[Any, Any]) -> None:
        self._transfer = transfer

    def __await__(self):
        return self._transfer.wait_processing().__await__()

    def cancel(self) -> None:
        self._transfer.cancel()


class _AcceptedInput:
    """Adapt accepted input without moving synchronous driving to a worker."""

    def __init__(
        self, driver: Any, accept: Callable[[Any], Any] | None, capacity: int,
    ) -> None:
        self._driver, self._accept = driver, accept
        self._accepted: asyncio.Queue[XTransfer[Any, Any]] | None = (
            asyncio.Queue(capacity) if isinstance(driver, SyncDriver) else None
        )

    def submit(self, request: Any) -> XTransfer[Any, Any]:
        if self._accepted is not None:
            return XTransfer(request)
        return self._driver.submit(request)

    async def drive(self, transfer: XTransfer[Any, Any]) -> XEvent:
        assert self._accepted is not None
        try:
            event = await self._driver.send(transfer.request)
            transfer.mark_processing(event)
            self._accepted.put_nowait(transfer)
            return event
        except asyncio.CancelledError:
            transfer.cancel()
            raise
        except BaseException as error:
            transfer.fail(error)
            transfer.observe_failure()  # This send caller receives the failure.
            raise

    async def recv_accepted(self) -> XTransfer[Any, Any]:
        if self._accepted is None:
            transfer = await self._driver.recv_accepted()
        else:
            transfer = await self._accepted.get()
            self._accepted.task_done()
        if self._accept is None:
            return transfer
        # Model advancement is independent from the checker's expected override.
        expected = self._accept(transfer.request)
        if inspect.isawaitable(expected):
            close = getattr(expected, "close", None)
            if close is not None:
                close()
            raise TypeError("reference model accept(request) must be synchronous")
        if transfer.expected is MISSING:
            transfer.set_expected(expected)
        return transfer


class Agent(Generic[DriverT]):
    """Encapsulate an interface; Execution owns its single run lifecycle.

    Construct and connect before entering ``Execution(backend, agents=[agent])``.
    Protocol subclasses build their driver/monitors and declare response timing.
    A response connection uses the driver's explicit acceptance event. Named
    observations not consumed by the checker remain available through recv.
    """

    def __init__(
        self, name: str, *, driver: DriverT | None = None,
        monitors: Mapping[str, Monitor[Any]] | None = None,
        response_monitor: str | None = None,
        clock: Any = None,
        response_timeout_cycles: int | None = None,
        latency_cycles: int | None = None,
        sampled: bool = False,
        request_key: Callable[[Any], Hashable] | None = None,
        response_key: Callable[[Any], Hashable] | None = None,
        compare: Callable[[Any, Any], None] | None = None,
        capacity: int = 64,
    ) -> None:
        if not name:
            raise ValueError("agent name cannot be empty")
        entries = dict(monitors or {})
        if driver is None and not entries:
            raise ValueError("agent requires a driver or monitor")
        if any(not isinstance(key, str) or not key for key in entries):
            raise ValueError("monitor names must be nonempty strings")
        if len({id(monitor) for monitor in entries.values()}) != len(entries):
            raise ValueError("each monitor must have a single owning name")
        if driver is not None and not all(
            callable(getattr(driver, method, None))
            for method in ("__aenter__", "__aexit__")
        ):
            raise TypeError("agent driver must support async context management")
        if response_monitor is not None:
            if response_monitor not in entries:
                raise ValueError("response_monitor must name an owned monitor")
            if not isinstance(driver, SyncDriver) and not all(
                callable(getattr(driver, method, None)) for method in ("submit", "recv_accepted")
            ):
                raise TypeError("response checking requires SyncDriver or submit/recv_accepted")
            if clock is None or response_timeout_cycles is None:
                raise ValueError("response checking requires explicit clock and response_timeout_cycles")
        elif (clock is not None or response_timeout_cycles is not None
              or latency_cycles is not None or sampled or request_key is not None
              or response_key is not None or compare is not None):
            raise ValueError("response options require response_monitor")
        self.name = name
        self._driver = driver
        self._monitors = MappingProxyType(entries)
        self._response_monitor = response_monitor
        self._response_options = dict(
            clock=clock, response_timeout_cycles=response_timeout_cycles,
            latency_cycles=latency_cycles, sampled=sampled,
            request_key=request_key, response_key=response_key,
        )
        self._board: Scoreboard[Any, Any, Any] | None = (
            Scoreboard(name, compare=compare, capacity=capacity)
            if response_monitor is not None else None
        )
        self._accept: Callable[[Any], Any] | None = None
        self._input: _AcceptedInput | None = None
        self._execution: Execution | None = None
        self._started_monitors: list[Monitor[Any]] = []
        self._driver_entered = False
        self._active = False
        self._closed = False

    @property
    def driver(self) -> DriverT | None:
        """Component inspection; transactions normally go through this Agent."""
        return self._driver

    @property
    def monitors(self) -> Mapping[str, Monitor[Any]]:
        return self._monitors

    def connect(self, ref: Any) -> Self:
        """Use ref.accept(request) on accepted input; do not own ref's lifecycle."""
        if self._execution is not None or self._closed or self._accept is not None:
            raise RuntimeError("connect requires a fresh, unconnected Agent")
        if self._board is None:
            raise RuntimeError("connect requires a configured response monitor and accepted stream")
        accept = getattr(ref, "accept", None)
        if not callable(accept) or inspect.iscoroutinefunction(accept):
            raise TypeError("reference model must provide synchronous accept(request)")
        self._accept = accept
        return self

    def send(self, request: Any, *, expected: Any = MISSING) -> Awaitable[XEvent]:
        """Drive input and await acceptance; a configured checker stays active."""
        self._require_active()
        if self._board is not None:
            if isinstance(self._driver, SyncDriver):
                return self._send_sync(request, expected=expected)
            return _Acceptance(self.submit(request, expected=expected))
        if expected is not MISSING:
            raise RuntimeError("expected requires a configured response checker")
        if self._driver is None:
            raise RuntimeError("passive Agent cannot send input")
        return self._driver.send(request)

    def submit(self, request: Any, *, expected: Any = MISSING) -> XTransfer[Any, Any]:
        """Submit now; the response transfer completes after checking."""
        if isinstance(self._driver, SyncDriver):
            raise RuntimeError("SyncDriver runs in the send caller; it does not support submit")
        return self._submit_response(request, expected=expected)

    def _submit_response(self, request: Any, *, expected: Any = MISSING) -> XTransfer[Any, Any]:
        board = self._require_board()
        if self._accept is None and expected is MISSING:
            raise RuntimeError("submit requires connect(ref) or an explicit expected value")
        return board.submit(request, expected=expected)

    async def _send_sync(self, request: Any, *, expected: Any) -> XEvent:
        transfer = self._submit_response(request, expected=expected)
        assert self._input is not None
        return await self._input.drive(transfer)

    async def recv(self, name: str) -> Any:
        """Receive from a named monitor not reserved by the response checker."""
        self._require_active()
        if name == self._response_monitor:
            raise RuntimeError("response monitor is exclusively consumed by the Agent checker")
        return await self._monitors[name].recv()

    @property
    def status(self) -> ScoreboardStatus:
        if self._board is None:
            raise RuntimeError("Agent has no response checker")
        return self._board.status

    async def drain(self, *, timeout_cycles: int) -> ScoreboardStatus:
        return await self._require_board().drain(timeout_cycles=timeout_cycles)

    async def finish(self, *, timeout_cycles: int, observe_cycles: int = 0) -> ScoreboardStatus:
        """Seal and check with a budget; Execution still owns all components."""
        return await self._require_board().finish(
            timeout_cycles=timeout_cycles, observe_cycles=observe_cycles,
        )

    def _require_active(self) -> None:
        if (not self._active or self._execution is None
                or self._execution.reactor is not current_reactor()):
            raise RuntimeError("Agent requires its owning active Execution")

    def _require_board(self) -> Scoreboard[Any, Any, Any]:
        self._require_active()
        if self._board is None:
            raise RuntimeError("Agent has no response checker; use send/recv for this interface")
        return self._board

    def _components(self) -> tuple[Any, ...]:
        return ((self._driver,) if self._driver is not None else ()) + tuple(self._monitors.values())

    async def _start(self, execution: Execution) -> None:
        if self._execution is not None or self._closed:
            raise RuntimeError(f"agent {self.name!r} is already started or closed")
        self._execution = execution
        try:
            for monitor in self._monitors.values():
                self._started_monitors.append(monitor)
                monitor.start(execution)
            if self._driver is not None:
                await self._driver.__aenter__()
                self._driver_entered = True
            if self._board is not None:
                self._input = _AcceptedInput(self._driver, self._accept, self._board.capacity)
                self._board.bind(
                    execution, driver=self._input,
                    monitor=self._monitors[self._response_monitor],
                    manage_monitor=False, **self._response_options,
                )
                await self._board.__aenter__()
            self._active = True
        except BaseException as error:
            await self._close(error)
            raise

    async def _close(self, primary: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        self._active = False
        errors: list[BaseException] = []


        # Check first while inputs and observations still exist; then release.
        if self._board is not None:
            try:
                await self._board._close(primary)
            except BaseException as error:
                errors.append(error)
        if self._driver_entered:
            self._driver_entered = False
            try:
                await self._driver.__aexit__(
                    type(primary) if primary is not None else None,
                    primary, primary.__traceback__ if primary is not None else None,
                )
            except BaseException as error:
                errors.append(error)
        while self._started_monitors:
            try:
                await self._started_monitors.pop().aclose()
            except BaseException as error:
                errors.append(error)
        raise_cleanup_errors(f"agent {self.name}: body and cleanup failures", errors, primary)
