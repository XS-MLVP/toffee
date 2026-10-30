"""Direct and asynchronous transaction checking."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Generic, Hashable, Literal, Protocol, Self, TypeVar

from .events import LogicValue, XEvent, XPhase
from ._reactor_context import current_reactor
from ._cleanup_errors import raise_cleanup_errors
from ._scoreboard_matching import Association, cycle_deadline
from .execution import Execution
from .reactor import Reactor
from .triggers import FallingEdge
from .interfaces import Transfer
from .transfers import MISSING, TransferState, XTransfer


RequestT = TypeVar("RequestT")
ExpectedT = TypeVar("ExpectedT")
ActualT = TypeVar("ActualT")


class AcceptedDriver(Protocol[RequestT, ActualT]):
    """Structural contract required by asynchronous Scoreboard mode."""

    def submit(self, request: RequestT) -> XTransfer[RequestT, ActualT]: ...

    async def recv_accepted(self) -> XTransfer[RequestT, ActualT]: ...


class ObservedMonitor(Protocol[ActualT]):
    def start(self, execution: Any) -> Any: ...

    async def recv(self) -> Transfer[ActualT]: ...

    async def aclose(self) -> None: ...


class ScoreboardMode(str, Enum):
    DIRECT = "DIRECT"
    ASYNC = "ASYNC"


@dataclass(frozen=True, slots=True)
class CheckContext:
    scoreboard: str
    mode: ScoreboardMode
    sequence_id: int | None = None
    request: object | None = None
    transfer_state: TransferState | None = None
    accepted_event: XEvent | None = None
    observed_event: XEvent | None = None


@dataclass(frozen=True, slots=True)
class Difference:
    path: str
    expected: Any
    actual: Any

    def __str__(self) -> str:
        return (
            f"{self.path}: expected {_render(self.expected)}, "
            f"actual {_render(self.actual)}"
        )


@dataclass(frozen=True, slots=True)
class ScoreboardStatus:
    name: str
    mode: ScoreboardMode
    submitted: int
    accepted: int
    observed: int
    checked: int
    passed: int
    failed: int
    pending: int
    processing: int
    completed: int
    cancelled: int
    unmatched_requests: int
    unmatched_observations: int
    first_failure: BaseException | None


class ScoreboardError(AssertionError):
    """Base for DUT-facing Scoreboard failures reported to pytest."""


class ScoreboardMismatch(ScoreboardError):
    def __init__(
        self,
        *,
        name: str,
        expected: Any,
        actual: Any,
        context: CheckContext,
        differences: Sequence[Difference],
        status: ScoreboardStatus,
    ) -> None:
        self.name = name
        self.expected = expected
        self.actual = actual
        self.context = context
        self.differences = tuple(differences)
        self.status = status
        super().__init__(self._message())

    def _message(self) -> str:
        context = self.context
        lines = [f"{self.name} mismatch", f"  mode: {context.mode.value}"]
        if context.sequence_id is not None:
            lines.append(f"  sequence_id: {context.sequence_id}")
        if context.transfer_state is not None:
            lines.append(f"  transfer_state: {context.transfer_state.value}")
        if context.request is not None:
            lines.append(f"  request: {context.request!r}")
        if context.accepted_event is not None:
            lines.append(f"  accepted_tick: {context.accepted_event.tick}")
        if context.observed_event is not None:
            lines.append(f"  observed_tick: {context.observed_event.tick}")
        lines.extend(
            (
                f"  expected: {_render(self.expected)}",
                f"  actual:   {_render(self.actual)}",
            )
        )
        if self.differences:
            lines.append("  differences:")
            lines.extend(f"    {difference}" for difference in self.differences)
        status = self.status
        lines.append(
            "  status: "
            f"checked={status.checked}, passed={status.passed}, "
            f"failed={status.failed}, pending={status.pending}, "
            f"processing={status.processing}"
        )
        return "\n".join(lines)


class ScoreboardAssociationError(ScoreboardError):
    pass


class ScoreboardIncompleteError(ScoreboardError):
    pass


class ScoreboardTimeoutError(ScoreboardError, TimeoutError):
    """A cycle budget failure with machine-readable timing and context."""

    def __init__(
        self,
        *,
        name: str,
        operation: Literal["response", "fixed_latency", "drain", "finish"],
        budget_cycles: int,
        start_tick: int,
        current_tick: int,
        context: CheckContext | None = None,
        status: ScoreboardStatus | None = None,
        reason: str = "deadline exceeded",
    ) -> None:
        self.name = name
        self.operation = operation
        self.budget_cycles = budget_cycles
        self.start_tick = start_tick
        self.deadline_tick = cycle_deadline(start_tick, budget_cycles)
        self.current_tick = current_tick
        self.elapsed_cycles = (current_tick - start_tick) / 2
        self.context = context
        self.status = status
        self.reason = reason
        label = "fixed-latency response" if operation == "fixed_latency" else operation
        message = (
            f"{name}: {label} deadline {self.deadline_tick}; {reason} "
            f"at tick {current_tick}; budget={budget_cycles} cycles, "
            f"elapsed={self.elapsed_cycles:g} cycles, start_tick={start_tick}"
        )
        if context is not None:
            message += (
                f"; sequence_id={context.sequence_id}, request={context.request!r}"
            )
            if context.accepted_event is not None:
                message += f", accepted_tick={context.accepted_event.tick}"
        if status is not None:
            message += f"; pending={status.pending}, processing={status.processing}"
        super().__init__(message)


class _ComparisonFailure(AssertionError):
    def __init__(self, differences: tuple[Difference, ...]) -> None:
        self.differences = differences
        super().__init__("; ".join(str(item) for item in differences))


class MissingExpectedError(RuntimeError):
    pass


Compare = Callable[[ExpectedT, ActualT], None]
ExpectedSource = Callable[[RequestT, CheckContext], ExpectedT]


class Scoreboard(Generic[RequestT, ExpectedT, ActualT]):
    """Check direct values or correlate asynchronous driver/monitor streams."""

    def __init__(
        self,
        name: str,
        *,
        compare: Compare[ExpectedT, ActualT] | None = None,
        expected: ExpectedSource[RequestT, ExpectedT] | None = None,
        max_differences: int = 20,
        capacity: int = 64,
    ) -> None:
        if not name:
            raise ValueError("Scoreboard name cannot be empty")
        if max_differences <= 0:
            raise ValueError("max_differences must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if expected is not None and (
            not callable(expected) or inspect.iscoroutinefunction(expected)
        ):
            raise TypeError("expected source must be synchronous and callable")
        self.capacity = capacity
        self.name = name
        self._compare = compare
        self._expected_source = expected
        self.max_differences = max_differences
        self._mode = ScoreboardMode.DIRECT
        self._execution: Execution | None = None
        self._driver: AcceptedDriver[RequestT, ActualT] | None = None
        self._monitor: ObservedMonitor[ActualT] | None = None
        self._clock: Any = None
        self._response_timeout_cycles = 0
        self._association = Association(None, None, None)
        self._sampled = False
        self._transfers: dict[int, tuple[int, XTransfer[RequestT, ActualT]]] = {}
        self._pending: list[XTransfer[RequestT, ActualT]] = []
        self._observations: list[Transfer[ActualT]] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._monitor_started = False
        self._manage_monitor = True
        self._monitor_consumer_reactor: Reactor | None = None
        self._sealed = False
        self._checkpoint_tick = -1
        self._completed = 0
        self._cancelled = 0
        self._failed_transfer: XTransfer[RequestT, ActualT] | None = None
        self._active = False
        self._closed = False
        self._changed = asyncio.Event()
        self._submitted = 0
        self._accepted = 0
        self._observed = 0
        self._checked = 0
        self._passed = 0
        self._failed = 0
        self._first_failure: BaseException | None = None
        self._async_failure: BaseException | None = None
        self._async_failure_observed = False

    @property
    def mode(self) -> ScoreboardMode:
        return self._mode

    @property
    def status(self) -> ScoreboardStatus:
        states = {state: 0 for state in TransferState}
        for _, transfer in self._transfers.values():
            states[transfer.state] += 1
        return ScoreboardStatus(
            name=self.name,
            mode=self._mode,
            submitted=self._submitted,
            accepted=self._accepted,
            observed=self._observed,
            checked=self._checked,
            passed=self._passed,
            failed=self._failed,
            pending=states[TransferState.PENDING],
            processing=states[TransferState.PROCESSING],
            completed=self._completed,
            cancelled=self._cancelled,
            unmatched_requests=len(self._pending),
            unmatched_observations=len(self._observations),
            first_failure=self._first_failure,
        )

    def compare(self, expected: ExpectedT, actual: ActualT) -> None:
        """Overridable value comparison, shared by direct and async modes."""

        if self._compare is not None:
            self._compare(expected, actual)
            return
        differences = structural_differences(
            expected,
            actual,
            limit=self.max_differences,
        )
        if differences:
            raise _ComparisonFailure(differences)

    def check(
        self,
        *,
        expected: ExpectedT,
        actual: ActualT,
        context: CheckContext | None = None,
    ) -> None:
        """Compare one pair and raise a contextual pytest-native failure."""

        if context is None:
            context = CheckContext(self.name, self._mode)
        self._checked += 1
        try:
            self.compare(expected, actual)
        except ScoreboardMismatch:
            self._failed += 1
            raise
        except AssertionError as cause:
            self._failed += 1
            differences = (
                cause.differences
                if isinstance(cause, _ComparisonFailure)
                else structural_differences(
                    expected, actual, limit=self.max_differences
                )
            )
            error = ScoreboardMismatch(
                name=self.name,
                expected=expected,
                actual=actual,
                context=context,
                differences=differences,
                status=self.status,
            )
            if self._first_failure is None:
                self._first_failure = error
            raise error from cause
        else:
            self._passed += 1

    def bind(
        self,
        execution: Execution,
        *,
        driver: AcceptedDriver[RequestT, ActualT],
        monitor: ObservedMonitor[ActualT],
        clock: Any,
        response_timeout_cycles: int,
        latency_cycles: int | None = None,
        sampled: bool = False,
        request_key: Callable[[RequestT], Hashable] | None = None,
        response_key: Callable[[ActualT], Hashable] | None = None,
        manage_monitor: bool = True,
    ) -> Self:
        if self._driver is not None or self._closed:
            raise RuntimeError(f"Scoreboard {self.name!r} is already bound or closed")
        if execution.reactor is None or execution.reactor is not current_reactor():
            raise RuntimeError("bind requires the current active Execution")
        _positive_cycles(response_timeout_cycles)
        if latency_cycles is not None:
            if not isinstance(latency_cycles, int) or latency_cycles < 0:
                raise ValueError("latency_cycles must be a non-negative integer")
            if latency_cycles > response_timeout_cycles:
                raise ValueError("latency_cycles exceeds response_timeout_cycles")
        if sampled and latency_cycles is None:
            raise ValueError("sampled mode requires latency_cycles")
        if (request_key is None) != (response_key is None):
            raise ValueError("request_key and response_key must be provided together")
        if latency_cycles is not None and request_key is not None:
            raise ValueError("latency_cycles and key association are mutually exclusive")
        # Validate the clock using the backend's own identity/domain checks.
        registration = execution.reactor.register(FallingEdge(clock))
        execution.reactor.cancel(registration)
        registration.future.cancel()
        self._execution = execution
        self._driver = driver
        self._monitor = monitor
        self._manage_monitor = manage_monitor
        self._clock = clock
        self._response_timeout_cycles = response_timeout_cycles
        self._association = Association(latency_cycles, request_key, response_key)
        self._sampled = sampled
        self._mode = ScoreboardMode.ASYNC
        return self

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError(f"Scoreboard {self.name!r} is closed")
        if self._driver is not None:
            self._ensure_active()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self._close(primary=exc)

    def submit(
        self, request: RequestT, *, expected: ExpectedT | Any = MISSING
    ) -> XTransfer[RequestT, ActualT]:
        if self._closed or self._sealed:
            raise RuntimeError(f"Scoreboard {self.name!r} no longer accepts submissions")
        self._raise_failure()
        driver = self._require_driver()
        if len(self._transfers) >= self.capacity:
            raise RuntimeError(f"{self.name} outstanding capacity is full")
        self._ensure_active()
        transfer = driver.submit(request)
        if not isinstance(transfer, XTransfer) or transfer.state is not TransferState.PENDING:
            raise TypeError("driver.submit() must return a pending XTransfer")
        if expected is not MISSING:
            transfer.set_expected(expected)
        self._submitted += 1
        self._transfers[transfer.sequence_id] = (self._submitted, transfer)
        transfer.add_failure_observer(self._on_failure_observed)
        transfer.add_done_callback(self._on_transfer_done)
        self._changed.set()
        return transfer

    def associate(
        self,
        observation: Transfer[ActualT],
        pending: Sequence[XTransfer[RequestT, ActualT]],
    ) -> XTransfer[RequestT, ActualT] | None:
        """Pure association hook. None retains an observation for later input."""
        return self._association.select(observation, pending)

    async def drain(self, *, timeout_cycles: int) -> ScoreboardStatus:
        """Wait for the submission watermark, within an explicit cycle budget."""
        _positive_cycles(timeout_cycles)
        self._require_driver()
        self._ensure_active()
        await self._drain_until(
            self._submitted, start_tick=self._execution.backend.tick,
            timeout_cycles=timeout_cycles, operation="drain",
        )
        return self.status

    async def _drain_until(
        self, watermark: int, *, start_tick: int,
        timeout_cycles: int, operation: Literal["drain", "finish"],
    ) -> None:
        deadline = cycle_deadline(start_tick, timeout_cycles)
        while True:
            self._raise_failure()
            if not any(ordinal <= watermark for ordinal, _ in self._transfers.values()):
                return
            if self._checkpoint_tick > deadline:
                self._record_failure(ScoreboardTimeoutError(
                    name=self.name, operation=operation, budget_cycles=timeout_cycles,
                    start_tick=start_tick, current_tick=self._checkpoint_tick,
                    status=self.status,
                ))
                self._raise_failure()
            self._changed.clear()
            await self._changed.wait()

    async def finish(
        self, *, timeout_cycles: int, observe_cycles: int = 0
    ) -> ScoreboardStatus:
        """Seal submissions, drain, observe a bounded tail, then check balance."""
        _positive_cycles(timeout_cycles)
        if not isinstance(observe_cycles, int) or observe_cycles < 0:
            raise ValueError("observe_cycles must be a non-negative integer")
        self._require_driver()
        self._ensure_active()
        self._sealed = True
        start_tick = self._execution.backend.tick
        deadline = cycle_deadline(start_tick, timeout_cycles)
        await self._drain_until(
            self._submitted, start_tick=start_tick,
            timeout_cycles=timeout_cycles, operation="finish",
        )
        if observe_cycles:
            end = cycle_deadline(self._execution.backend.tick, observe_cycles)
            if end > deadline:
                self._record_failure(ScoreboardTimeoutError(
                    name=self.name, operation="finish", budget_cycles=timeout_cycles,
                    start_tick=start_tick, current_tick=self._execution.backend.tick,
                    status=self.status,
                    reason=f"observation window ({observe_cycles} cycles) exceeds finish deadline",
                ))
                self._raise_failure()
            while self._checkpoint_tick <= end:
                self._raise_failure()
                if self._checkpoint_tick > deadline:
                    self._record_failure(ScoreboardTimeoutError(
                        name=self.name, operation="finish", budget_cycles=timeout_cycles,
                        start_tick=start_tick, current_tick=self._checkpoint_tick,
                        status=self.status, reason="observation deadline exceeded",
                    ))
                    self._raise_failure()
                self._changed.clear()
                await self._changed.wait()
        async with self._execution.paused():
            await asyncio.sleep(0)
            self._raise_failure()
            error = self._balance_error()
            if error is not None:
                self._record_failure(error)
                self._raise_failure()
        return self.status

    async def aclose(self) -> None:
        """Check current balance and release resources without draining DUT work."""
        await self._close()

    async def _close(self, primary: BaseException | None = None) -> None:
        if self._closed:
            return
        self._sealed = True
        errors: list[BaseException] = []
        # Freeze simulation while already-ready source consumers run. They do
        # no asynchronous work between receiving a value and reconciling it.
        if self._active:
            async with self._execution.paused():
                await asyncio.sleep(0)
                errors.extend(self._close_errors(primary))
                self._closed = True
                await self._stop_tasks()
        else:
            errors.extend(self._close_errors(primary))
            self._closed = True
            await self._stop_tasks()
        if self._monitor_started:
            self._monitor_started = False
            try:
                assert self._monitor is not None
                await self._monitor.aclose()
            except BaseException as error:
                errors.append(error)
        if self._monitor_consumer_reactor is not None:
            self._monitor_consumer_reactor._release_monitor_consumer(self._monitor, self)
            self._monitor_consumer_reactor = None
        self._active = False
        self._observations.clear()
        self._pending.clear()
        self._changed.set()
        raise_cleanup_errors(f"{self.name}: body and cleanup failures", errors, primary)

    def _close_errors(self, primary: BaseException | None) -> list[BaseException]:
        errors: list[BaseException] = []
        if self._async_failure is not None:
            if not self._async_failure_observed:
                errors.append(self._async_failure)
                self._mark_async_failure_observed()
        elif primary is None:
            error = self._balance_error()
            if error is not None:
                errors.append(error)
        reason = primary or (errors[0] if errors else self._async_failure)
        for _, transfer in tuple(self._transfers.values()):
            if transfer.state is TransferState.PENDING:
                transfer.cancel()
            elif transfer.state is TransferState.PROCESSING:
                transfer.fail(reason or ScoreboardIncompleteError(
                    f"{self.name}: closing with incomplete accepted transfer"
                ))
        # The close boundary itself reports these failures.
        self._mark_async_failure_observed()
        return errors

    def _balance_error(self) -> BaseException | None:
        if self._transfers:
            return ScoreboardIncompleteError(
                f"{self.name}: {len(self._transfers)} incomplete transfer(s): "
                + ", ".join(str(key) for key in tuple(self._transfers)[:8])
            )
        if not self._sampled and self._observations:
            return ScoreboardAssociationError(
                f"{self.name}: {len(self._observations)} unmatched observation(s); "
                f"first={self._observations[0]!r}"
            )
        return None

    def _require_driver(self) -> AcceptedDriver[RequestT, ActualT]:
        if self._driver is None or self._monitor is None:
            raise RuntimeError(f"Scoreboard {self.name!r} must be bound")
        return self._driver

    def _ensure_active(self) -> None:
        if self._closed:
            raise RuntimeError(f"Scoreboard {self.name!r} is closed")
        self._raise_failure()
        if (
            self._execution is None
            or self._execution.reactor is not current_reactor()
            or self._execution._closing
        ):
            raise RuntimeError("Scoreboard requires its bound active Execution")
        if self._active:
            return
        driver = self._require_driver()
        assert self._monitor is not None
        try:
            reactor = self._execution.reactor
            assert reactor is not None
            reactor._claim_monitor_consumer(self._monitor, self)
            self._monitor_consumer_reactor = reactor
            if self._manage_monitor:
                self._monitor_started = True  # Clean up partially failed start().
                self._monitor.start(self._execution)
            self._active = True
            for label, coroutine in (
                ("accepted", self._pump_accepted(driver)),
                ("observed", self._pump_observed(self._monitor)),
                ("deadlines", self._watch_clock()),
            ):
                self._tasks.append(asyncio.create_task(
                    coroutine, name=f"{self.name}-{label}"
                ))
        except BaseException as error:
            self._record_failure(error)
            self._mark_async_failure_observed()
            raise

    async def _pump_accepted(self, driver: AcceptedDriver[RequestT, ActualT]) -> None:
        try:
            while True:
                self._accept(await driver.recv_accepted())
                self._reconcile()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._record_failure(error)

    async def _pump_observed(self, monitor: ObservedMonitor[ActualT]) -> None:
        try:
            while True:
                observation = await monitor.recv()
                self._validate_event(observation.event)
                self._observed += 1
                if len(self._observations) >= self.capacity:
                    raise ScoreboardAssociationError(
                        f"{self.name}: unmatched observation capacity {self.capacity} exceeded"
                    )
                self._observations.append(observation)
                self._reconcile()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._record_failure(error)

    async def _watch_clock(self) -> None:
        try:
            while True:
                event = await FallingEdge(self._clock)
                # Check on the falling barrier AFTER the inclusive rising
                # deadline. Pause also lets both source pumps consume ready
                # records before checking; no simulator step is allowed here.
                async with self._execution.paused():
                    await asyncio.sleep(0)
                    self._check_deadlines(event.tick)
                    self._prune_samples(event.tick)
                    self._checkpoint_tick = event.tick
                    self._changed.set()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._record_failure(error)

    def _validate_event(self, event: XEvent) -> None:
        if event.phase is not XPhase.RISING_STABLE or event.tick < 0:
            raise ScoreboardAssociationError(
                f"{self.name}: timed transactions require rising-stable events, got {event!r}"
            )

    def _accept(self, transfer: XTransfer[RequestT, ActualT]) -> None:
        if transfer.state is TransferState.CANCELLED:
            return
        entry = self._transfers.get(transfer.sequence_id)
        if entry is None or entry[1] is not transfer or transfer in self._pending:
            raise ScoreboardAssociationError(
                f"{self.name}: unknown or duplicate accepted transfer"
            )
        if transfer.state is not TransferState.PROCESSING or transfer.accepted_event is None:
            raise ScoreboardAssociationError(f"{self.name}: accepted transfer is not processing")
        self._validate_event(transfer.accepted_event)
        key = self._association.request_key
        if key is not None and any(
            key(item.request) == key(transfer.request) for item in self._pending
        ):
            raise ScoreboardAssociationError(
                f"{self.name}: duplicate pending association key {key(transfer.request)!r}"
            )
        self._accepted += 1
        context = self._context(transfer)
        if transfer.expected is MISSING:
            if self._expected_source is None:
                raise MissingExpectedError(
                    f"{self.name}: transfer {transfer.sequence_id} has no expected value or source"
                )
            expected = self._expected_source(transfer.request, context)
            if inspect.isawaitable(expected):
                close = getattr(expected, "close", None)
                if close is not None:
                    close()
                raise TypeError("expected source must be synchronous")
            transfer.set_expected(expected)
        self._pending.append(transfer)

    def _context(
        self, transfer: XTransfer[RequestT, ActualT], observed: XEvent | None = None
    ) -> CheckContext:
        return CheckContext(
            self.name, self._mode, transfer.sequence_id, transfer.request,
            transfer.state, transfer.accepted_event, observed,
        )

    def _deadline(self, transfer: XTransfer[RequestT, ActualT]) -> int:
        assert transfer.accepted_event is not None
        cycles = self._association.latency_cycles
        if cycles is None:
            cycles = self._response_timeout_cycles
        return cycle_deadline(transfer.accepted_event.tick, cycles)

    def _timeout(
        self, transfer: XTransfer[RequestT, ActualT], tick: int
    ) -> ScoreboardTimeoutError:
        assert transfer.accepted_event is not None
        latency = self._association.latency_cycles
        return ScoreboardTimeoutError(
            name=self.name,
            operation="response" if latency is None else "fixed_latency",
            budget_cycles=self._response_timeout_cycles if latency is None else latency,
            start_tick=transfer.accepted_event.tick, current_tick=tick,
            context=self._context(transfer), status=self.status,
        )

    def _reconcile(self) -> None:
        for observation in tuple(self._observations):
            transfer = self.associate(observation, tuple(self._pending))
            if transfer is None:
                continue
            if transfer not in self._pending:
                raise ScoreboardAssociationError(
                    f"{self.name}: associate returned a non-pending transfer"
                )
            assert transfer.accepted_event is not None
            if observation.event.tick < transfer.accepted_event.tick:
                continue
            if observation.event.tick > self._deadline(transfer):
                raise self._timeout(transfer, observation.event.tick)
            self.check(
                expected=transfer.expected, actual=observation.value,
                context=self._context(transfer, observation.event),
            )
            self._observations.remove(observation)
            transfer.complete(observation.event, observation.value)

    def _check_deadlines(self, tick: int) -> None:
        for transfer in self._pending:
            if tick > self._deadline(transfer):
                raise self._timeout(transfer, tick)

    def _prune_samples(self, tick: int) -> None:
        if not self._sampled:
            return
        # Include accepted handles whose source notification is still queued.
        # Samples older than this stable checkpoint cannot belong to a future
        # acceptance, even while some submitted inputs are still pending.
        due = {
            self._deadline(item) for _, item in self._transfers.values()
            if item.state is TransferState.PROCESSING
        }
        self._observations[:] = [
            item for item in self._observations
            if item.event.tick in due or item.event.tick >= tick
        ]

    def _on_transfer_done(self, transfer: XTransfer[RequestT, ActualT]) -> None:
        self._transfers.pop(transfer.sequence_id, None)
        if transfer in self._pending:
            self._pending.remove(transfer)
        if transfer.state is TransferState.COMPLETED:
            self._completed += 1
        elif transfer.state is TransferState.CANCELLED:
            self._cancelled += 1
        elif transfer.state is TransferState.FAILED:
            if self._failed_transfer is None:
                self._failed_transfer = transfer
            assert transfer.error is not None
            self._record_failure(transfer.error)
        self._changed.set()

    def _record_failure(self, error: BaseException) -> None:
        if self._async_failure is not None:
            return
        self._async_failure = error
        self._first_failure = self._first_failure or error
        self._sealed = True
        for _, transfer in tuple(self._transfers.values()):
            transfer.fail(error)
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        self._changed.set()

    def _raise_failure(self) -> None:
        if self._async_failure is not None:
            self._mark_async_failure_observed()
            raise self._async_failure

    async def _stop_tasks(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _on_failure_observed(
        self, transfer: XTransfer[RequestT, ActualT], error: BaseException
    ) -> None:
        if error is self._async_failure:
            self._async_failure_observed = True

    def _mark_async_failure_observed(self) -> None:
        self._async_failure_observed = True
        if self._failed_transfer is not None:
            self._failed_transfer.observe_failure()
            self._failed_transfer = None


def _positive_cycles(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("timeout_cycles must be a positive integer")


_PLAIN_SCALARS = frozenset((type(None), bool, int, float, complex, str, bytes, bytearray))


def _plain_equal(wanted: Any, got: Any) -> bool:
    """Positive-only fast path for ordinary sampled Python values.

    Do not trust container equality for arbitrary objects: dataclass equality
    may omit fields and list equality accepts identical NaNs. Unsupported
    values and mismatches always use the diagnostic structural walk below.
    """
    wanted_type, got_type = type(wanted), type(got)
    if wanted_type in _PLAIN_SCALARS and got_type in _PLAIN_SCALARS:
        return wanted == got
    if wanted_type in (list, tuple) and got_type in (list, tuple):
        return len(wanted) == len(got) and all(
            _plain_equal(left, right) for left, right in zip(wanted, got)
        )
    if wanted_type is dict and got_type is dict:
        return wanted.keys() == got.keys() and all(
            _plain_equal(value, got[key]) for key, value in wanted.items()
        )
    return False


def structural_differences(
    expected: Any,
    actual: Any,
    *,
    limit: int = 20,
) -> tuple[Difference, ...]:
    """Return deterministic leaf differences for common transaction values."""

    if limit > 0 and _plain_equal(expected, actual):
        return ()
    differences: list[Difference] = []

    def visit(wanted: Any, got: Any, path: str) -> None:
        if len(differences) >= limit:
            return
        if is_dataclass(wanted) and not isinstance(wanted, type):
            if not is_dataclass(got) or type(wanted) is not type(got):
                differences.append(Difference(path, wanted, got))
                return
            for field in fields(wanted):
                visit(
                    getattr(wanted, field.name),
                    getattr(got, field.name),
                    f"{path}.{field.name}" if path else field.name,
                )
            return
        if isinstance(wanted, Mapping):
            if not isinstance(got, Mapping):
                differences.append(Difference(path, wanted, got))
                return
            keys = list(wanted)
            keys.extend(key for key in got if key not in wanted)
            for key in keys:
                if len(differences) >= limit:
                    break
                child = f"{path}.{key}" if path else str(key)
                if key not in wanted:
                    differences.append(Difference(child, MISSING, got[key]))
                elif key not in got:
                    differences.append(Difference(child, wanted[key], MISSING))
                else:
                    visit(wanted[key], got[key], child)
            return
        if (
            isinstance(wanted, Sequence)
            and not isinstance(wanted, (str, bytes, bytearray))
        ):
            if not isinstance(got, Sequence) or isinstance(
                got, (str, bytes, bytearray)
            ):
                differences.append(Difference(path, wanted, got))
                return
            for index in range(max(len(wanted), len(got))):
                if len(differences) >= limit:
                    break
                child = f"{path}[{index}]"
                if index >= len(wanted):
                    differences.append(Difference(child, MISSING, got[index]))
                elif index >= len(got):
                    differences.append(Difference(child, wanted[index], MISSING))
                else:
                    visit(wanted[index], got[index], child)
            return
        try:
            equal = wanted == got
        except BaseException:
            equal = False
        if not equal:
            differences.append(Difference(path or "value", wanted, got))

    visit(expected, actual, "")
    return tuple(differences)


def _render(value: Any) -> str:
    if isinstance(value, LogicValue):
        if value.is_known:
            return f"0x{value.value:x}<{value.width}>"
        return (
            f"LogicValue(value=0x{value.value:x}, "
            f"x_mask=0x{value.x_mask:x}, width={value.width})"
        )
    return repr(value)


__all__ = [
    "AcceptedDriver",
    "CheckContext",
    "Difference",
    "MissingExpectedError",
    "ObservedMonitor",
    "Scoreboard",
    "ScoreboardAssociationError",
    "ScoreboardError",
    "ScoreboardIncompleteError",
    "ScoreboardMismatch",
    "ScoreboardMode",
    "ScoreboardStatus",
    "ScoreboardTimeoutError",
    "structural_differences",
]
