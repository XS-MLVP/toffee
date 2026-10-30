from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import asynccontextmanager
from contextvars import Context, Token, copy_context
from typing import TYPE_CHECKING, Any, AsyncIterator, Coroutine, TypeVar

from ._reactor_context import bind_reactor, current_reactor, reset_reactor
from ._asyncio_observer import CallbackDomain, current_domain
from ._cleanup_errors import raise_cleanup_errors
from .backend import RunLimit, SimulationBackend
from .events import XPhase
from .reactor import Reactor
from .reactor import Subscription
from .subscriptions import BoundSubscriptionSpec
from .triggers import DriveStable, FallingEdge, PhaseTrigger, RisingEdge

T = TypeVar("T")

if TYPE_CHECKING:
    from .agent import Agent


class Execution:
    def __init__(
        self,
        backend: SimulationBackend,
        *,
        agents: Iterable[Agent[Any]] = (),
        coverage: Iterable[Any] = (),
        default_sample: PhaseTrigger | None = None,
        max_batch_ticks: int = 4096,
        quantum_ms: float = 10.0,
        budget_check_interval: int = 64,
        max_settle_rounds: int = 100_000,
    ) -> None:
        if max_batch_ticks <= 0:
            raise ValueError("max_batch_ticks must be positive")
        if quantum_ms <= 0:
            raise ValueError("quantum_ms must be positive")
        if budget_check_interval <= 0:
            raise ValueError("budget_check_interval must be positive")
        if not isinstance(max_settle_rounds, int) or isinstance(max_settle_rounds, bool):
            raise TypeError("max_settle_rounds must be an integer")
        if max_settle_rounds <= 0:
            raise ValueError("max_settle_rounds must be positive")
        if default_sample is not None and not isinstance(
            default_sample, (RisingEdge, FallingEdge, DriveStable)
        ):
            raise TypeError(
                "default_sample must be RisingEdge, FallingEdge, or "
                "DriveStable"
            )
        capabilities = getattr(backend, "capabilities", None)
        if capabilities is None:
            raise TypeError("simulation backend must declare capabilities")
        if not capabilities.half_step or not capabilities.stable_sample:
            raise RuntimeError(
                "simulation backend must provide half-step stable sampling"
            )
        self.backend = backend
        from .agent import Agent

        self._agents = tuple(agents)
        if any(not isinstance(agent, Agent) for agent in self._agents):
            raise TypeError("Execution agents must be Agent instances")
        owned = [component for agent in self._agents for component in agent._components()]
        if (len({id(agent) for agent in self._agents}) != len(self._agents)
                or len({id(component) for component in owned}) != len(owned)):
            raise ValueError("Execution agents must own distinct components")
        self._started_agents: list[Agent[Any]] = []
        from .coverage import CoverGroup
        self._coverage = tuple(coverage)
        if any(not isinstance(group, CoverGroup) for group in self._coverage):
            raise TypeError("Execution coverage must contain CoverGroup instances")
        if len({id(group) for group in self._coverage}) != len(self._coverage):
            raise ValueError("Execution coverage must contain distinct instances")
        self._started_coverage: list[Any] = []
        self.default_sample = default_sample
        self.max_batch_ticks = max_batch_ticks
        self.quantum_ms = quantum_ms
        self.budget_check_interval = budget_check_interval
        self.max_settle_rounds = max_settle_rounds
        self.reactor: Reactor | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._context_token: Token[Reactor | None] | None = None
        self._domain: CallbackDomain | None = None
        self._domain_token: Token[CallbackDomain | None] | None = None
        self._delivery_context: Context | None = None
        self._closing = False
        self._pause_depth = 0
        self._resume = asyncio.Event()
        self._resume.set()

    async def __aenter__(self) -> "Execution":
        if self.reactor is not None:
            raise RuntimeError("Execution cannot be entered twice")
        self.backend.acquire(self)
        try:
            self.reactor = Reactor(
                self.backend, default_sample=self.default_sample
            )
            self._domain = CallbackDomain(self.max_settle_rounds)
            self._context_token = bind_reactor(self.reactor)
            self._domain_token = current_domain.set(self._domain)
            self._delivery_context = copy_context()
            for group in self._coverage:
                group._start(self)
                self._started_coverage.append(group)
            pump_context = copy_context()
            pump_context.run(current_domain.set, None)
            pump = self._run_pump()
            try:
                self._pump_task = asyncio.create_task(
                    pump, name="xreactor-execution-pump", context=pump_context,
                )
            except BaseException:
                pump.close()
                raise
            for agent in self._agents:
                # _start rolls back its own partial initialization on failure.
                await agent._start(self)
                self._started_agents.append(agent)
        except BaseException as error:
            await self._close(error)
            raise
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self._close(exc)

    async def _close(self, primary: BaseException | None = None) -> None:
        errors: list[BaseException] = []


        # Components need the live reactor while checking and releasing work.
        while self._started_agents:
            try:
                await self._started_agents.pop()._close(primary)
            except BaseException as error:
                errors.append(error)
        self._closing = True
        self._resume.set()
        if self._domain is not None:
            self._domain.stop()
        if self.reactor is not None:
            self.reactor.work_available.set()
        try:
            if self._pump_task is not None:
                await self._pump_task
        except BaseException as error:
            errors.append(error)
        # Advancement has stopped. Synchronize while the native backend and
        # registrations are alive, then release every collector even on error.
        while self._started_coverage:
            try:
                self._started_coverage.pop()._stop()
            except BaseException as error:
                errors.append(error)
        try:
            if self.reactor is not None:
                await self.reactor.aclose()
        except BaseException as error:
            errors.append(error)
        for cleanup in (self.backend.clear_execution_state, self._release_context,
                        lambda: self.backend.release(self)):
            try:
                cleanup()
            except BaseException as error:
                errors.append(error)
        raise_cleanup_errors("Execution: body and cleanup failures", errors, primary)

    def _release_context(self) -> None:
        try:
            if self._context_token is not None:
                reset_reactor(self._context_token)
                self._context_token = None
        finally:
            try:
                if self._domain_token is not None:
                    current_domain.reset(self._domain_token)
                    self._domain_token = None
            finally:
                if self._domain is not None:
                    self._domain.close()
                self._delivery_context = None

    def external_task(
        self, coro: Coroutine[Any, Any, T], *, name: str | None = None,
    ) -> asyncio.Task[T]:
        """Create a caller-owned native Task outside simulation observation.

        The task and its descendants have no implicit reactor binding. Awaiting
        it retains normal asyncio cancellation; TaskComplete can shield it.
        """
        if self.reactor is None or self._closing or current_reactor() is not self.reactor:
            raise RuntimeError("external_task requires the current active Execution")
        context = copy_context()
        context.run(current_domain.set, None)
        context.run(bind_reactor, None)
        return asyncio.create_task(coro, name=name, context=context)

    @asynccontextmanager
    async def paused(self) -> AsyncIterator[None]:
        self._pause_depth += 1
        self._resume.clear()
        try:
            await asyncio.sleep(0)
            yield
        finally:
            self._pause_depth -= 1
            if self._pause_depth == 0:
                self._resume.set()

    def subscribe(self, spec: BoundSubscriptionSpec) -> Subscription:
        if self.reactor is None:
            raise RuntimeError("subscribe requires an active Execution")
        return self.reactor.subscribe(spec)

    async def _ready_to_advance(self) -> None:
        assert self._domain is not None
        while not self._closing:
            await self._resume.wait()
            await self._domain.settle(self.backend)
            if not self._pause_depth:
                return

    def _advance(self):
        assert self.reactor is not None
        result = self.backend.run_until(
            RunLimit(
                max_ticks=self.max_batch_ticks,
                max_wall_time_ms=self.quantum_ms,
                budget_check_interval=self.budget_check_interval,
            )
        )
        if result.advanced_ticks == 0 and not result.hits:
            raise RuntimeError(f"simulation backend stalled: {result.stop_reason}")
        self.reactor.publish(result)
        return result

    def _sample_drive(self) -> None:
        assert self.reactor is not None
        result = self.backend.sample_drive_stable()
        if result is not None:
            self.reactor.publish(result)

    async def _run_pump(self) -> None:
        assert self.reactor is not None
        assert self._delivery_context is not None
        try:
            while not self._closing:
                await self.reactor.work_available.wait()
                self.reactor.raise_background_error()
                if self._closing:
                    break
                await self._ready_to_advance()
                if self._closing:
                    break
                if not self.reactor.has_execution_work:
                    self.reactor.work_available.clear()
                    continue
                # User callbacks inside backend evaluation/capture belong to
                # the Execution; the Pump's own continuation remains outside.
                result = self._delivery_context.run(self._advance)
                # A phase barrier must give resolved waiters a chance to run
                # before this task may enter the backend again.
                await asyncio.sleep(0)
                await self._ready_to_advance()
                if self._closing:
                    break
                self.reactor.raise_background_error()
                if (
                    result.stopped_phase is XPhase.FALLING_STABLE
                    and (self.reactor.has_drive_stable_work
                         or getattr(self.backend, "has_coverage_drive_work", False))
                ):
                    self._delivery_context.run(self._sample_drive)
                    await asyncio.sleep(0)
                    self.reactor.raise_background_error()
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError):
                self.reactor.fail(error)
            raise
