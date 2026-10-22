from __future__ import annotations

from dataclasses import dataclass, field, replace
from collections import OrderedDict
from contextlib import contextmanager
from enum import Enum
import importlib
import time
from typing import Any, Callable, Protocol

from .events import LogicValue, XEventKind, XPhase
from .signals import read_signal, sample_signal, signal_identity
from .ir import (
    BoundSignalExpr,
    BinaryExpr,
    BitsConstExpr,
    BitsType,
    BoolType,
    CastExpr,
    ConstantExpr,
    FsmSpec,
    HoldStep,
    NextStep,
    SequenceSpec,
    SignalExpr,
    UnaryExpr,
    WaitStep,
    WithinStep,
    XExpr,
    resolve_path,
    _SequenceState,
    _advance_sequence,
    _condition_known,
    _evaluate_condition,
    _operand_types,
    _uses_typed,
    expression_type,
)
from .triggers import (
    ClockCycles,
    CompiledTrigger,
    ConditionMode,
    DriveStable,
    FallingEdge,
    PythonPredicateTrigger,
    RisingEdge,
    SimTimeout,
    Value,
    ValueChange,
    XTrigger,
)


@dataclass(frozen=True, slots=True)
class BackendHandle:
    slot: int
    generation: int


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Execution properties beyond the required half-step/stable contract."""

    half_step: bool
    stable_sample: bool
    thread_safe: bool = False
    reentrant: bool = False
    multiple_instances: bool = True
    drive_stable: bool = False


class StopReason(str, Enum):
    EDGE_BARRIER = "EDGE_BARRIER"
    TRIGGER_HIT = "TRIGGER_HIT"
    RUN_LIMIT = "RUN_LIMIT"
    QUANTUM_EXPIRED = "QUANTUM_EXPIRED"
    PYTHON_SAMPLE = "PYTHON_SAMPLE"
    USER_PAUSE = "USER_PAUSE"
    SIMULATION_CLOSE = "SIMULATION_CLOSE"
    BACKEND_STOP = "BACKEND_STOP"
    CALLBACK_ERROR = "CALLBACK_ERROR"
    BACKEND_ERROR = "BACKEND_ERROR"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BackendHit:
    event_id: int
    tick: int
    slot: int
    generation: int
    kind: XEventKind
    phase: XPhase
    source: Any = None
    value: Any = None
    x_mask: int = 0
    terminal_state: str | None = None


@dataclass(frozen=True, slots=True)
class RunLimit:
    max_ticks: int = 1
    max_wall_time_ms: float | None = None
    budget_check_interval: int = 64

    def __post_init__(self) -> None:
        if self.max_ticks <= 0:
            raise ValueError("RunLimit.max_ticks must be positive")
        if self.max_wall_time_ms is not None and self.max_wall_time_ms <= 0:
            raise ValueError("RunLimit.max_wall_time_ms must be positive")
        if self.budget_check_interval <= 0:
            raise ValueError("budget_check_interval must be positive")


@dataclass(frozen=True, slots=True)
class RunResult:
    advanced_ticks: int
    stopped_phase: XPhase
    stop_reason: StopReason
    hits: tuple[BackendHit, ...] = ()
    is_phase_barrier: bool = True


class SimulationBackend(Protocol):
    capabilities: BackendCapabilities

    def acquire(self, owner: object) -> None: ...

    def release(self, owner: object) -> None: ...

    def arm(self, trigger: XTrigger[Any]) -> BackendHandle: ...

    def disarm(self, handle: BackendHandle) -> bool: ...

    def rearm(self, handle: BackendHandle) -> bool: ...

    def run_until(self, limit: RunLimit) -> RunResult: ...

    def clear_execution_state(self) -> None: ...

    def close(self) -> None: ...

    def refresh_comb(self) -> None: ...

    def sample_drive_stable(self) -> RunResult | None: ...


@dataclass(slots=True)
class _FsmState:
    current: str | None = None


@dataclass(slots=True)
class _Watcher:
    trigger: XTrigger[Any]
    generation: int
    remaining: int | None = None
    sequence: _SequenceState = field(default_factory=_SequenceState)
    fsm: _FsmState = field(default_factory=_FsmState)
    last_value: Any = None
    last_condition: bool = False


class MemoryBackend:
    """Deterministic half-step backend used to test framework semantics.

    It deliberately executes Python expressions and is not a performance model
    for the future xcomm adapter.
    """

    capabilities = BackendCapabilities(
        half_step=True,
        stable_sample=True,
        drive_stable=True,
        reentrant=False,
    )

    def __init__(
        self,
        clock: Any,
        *,
        on_phase: Callable[[XPhase, int], None] | None = None,
    ) -> None:
        self.clock = clock
        self.on_phase = on_phase
        self.tick = 0
        self.phase = XPhase.RISING_STABLE
        self._watchers: dict[int, _Watcher] = {}
        self._generations: list[int] = []
        self._free_slots: list[int] = []
        self._next_event_id = 1
        self._closed = False
        self._owner: object | None = None
        self._coverage_collectors: list[Any] = []

    def acquire(self, owner: object) -> None:
        if self._owner is not None and self._owner is not owner:
            raise RuntimeError("backend instance already has an active Execution")
        self._owner = owner

    def release(self, owner: object) -> None:
        if self._owner is owner:
            self._owner = None

    def arm(self, trigger: XTrigger[Any]) -> BackendHandle:
        if self._closed:
            raise RuntimeError("backend is closed")
        if self._free_slots:
            slot = self._free_slots.pop()
            self._generations[slot] += 1
        else:
            slot = len(self._generations)
            self._generations.append(0)
        generation = self._generations[slot]
        remaining = (
            trigger.cycles
            if isinstance(trigger, (ClockCycles, SimTimeout))
            else None
        )
        last_value = (
            getattr(trigger.signal, "value", trigger.signal)
            if isinstance(trigger, ValueChange)
            else None
        )
        self._watchers[slot] = _Watcher(
            trigger, generation, remaining, last_value=last_value
        )
        return BackendHandle(slot, generation)

    def disarm(self, handle: BackendHandle) -> bool:
        watcher = self._watchers.get(handle.slot)
        if watcher is None or watcher.generation != handle.generation:
            return False
        del self._watchers[handle.slot]
        self._free_slots.append(handle.slot)
        return True

    def rearm(self, handle: BackendHandle) -> bool:
        watcher = self._watchers.get(handle.slot)
        if watcher is None or watcher.generation != handle.generation:
            return False
        if isinstance(watcher.trigger, (ClockCycles, SimTimeout)):
            watcher.remaining = watcher.trigger.cycles
        if isinstance(watcher.trigger, ValueChange):
            watcher.last_value = getattr(
                watcher.trigger.signal, "value", watcher.trigger.signal
            )
        watcher.sequence = _SequenceState()
        watcher.fsm = _FsmState()
        return True

    def run_until(self, limit: RunLimit) -> RunResult:
        if self._closed:
            raise RuntimeError("backend is closed")
        all_hits: list[BackendHit] = []
        advanced = 0
        started_ns = time.perf_counter_ns()
        for _ in range(limit.max_ticks):
            self.tick += 1
            advanced += 1
            self.phase = (
                XPhase.FALLING_STABLE
                if self.tick % 2
                else XPhase.RISING_STABLE
            )
            if self.on_phase is not None:
                self.on_phase(self.phase, self.tick)
            hits = self._evaluate_phase()
            all_hits.extend(hits)
            if hits:
                reason = (
                    StopReason.EDGE_BARRIER
                    if any(
                        hit.kind
                        in (XEventKind.CLOCK_RISE, XEventKind.CLOCK_FALL)
                        for hit in hits
                    )
                    else StopReason.TRIGGER_HIT
                )
                return RunResult(
                    advanced,
                    self.phase,
                    reason,
                    tuple(all_hits),
                    True,
                )
            if self.phase is XPhase.FALLING_STABLE and (self.has_coverage_drive_work or any(
                _trigger_uses_drive_stable(watcher.trigger)
                for watcher in self._watchers.values()
            )):
                return RunResult(
                    advanced,
                    self.phase,
                    StopReason.EDGE_BARRIER,
                    tuple(all_hits),
                    True,
                )
            if (
                limit.max_wall_time_ms is not None
                and advanced % limit.budget_check_interval == 0
                and time.perf_counter_ns() - started_ns
                >= int(limit.max_wall_time_ms * 1_000_000)
            ):
                return RunResult(
                    advanced,
                    self.phase,
                    StopReason.QUANTUM_EXPIRED,
                    tuple(all_hits),
                    True,
                )
        return RunResult(
            advanced,
            self.phase,
            StopReason.RUN_LIMIT,
            tuple(all_hits),
            True,
        )

    def clear_execution_state(self) -> None:
        """Clear state owned by one Execution while keeping the clock alive."""

        if self._watchers or self._coverage_collectors:
            raise RuntimeError(
                "cannot reset backend runtime with active watchers"
            )

    def close(self) -> None:
        if self._coverage_collectors:
            raise RuntimeError("close the owning Execution before closing its coverage backend")
        self._watchers.clear()
        self._closed = True

    def refresh_comb(self) -> None:
        """MemoryBackend signals are ordinary Python values."""

    def sample_drive_stable(self) -> RunResult | None:
        if not self.has_coverage_drive_work and not any(
            _trigger_uses_drive_stable(watcher.trigger)
            for watcher in self._watchers.values()
        ):
            return None
        if self.phase is not XPhase.FALLING_STABLE:
            raise RuntimeError(
                "DriveStable sampling requires a preceding FallingStable"
            )
        self.phase = XPhase.DRIVE_STABLE
        hits = tuple(self._evaluate_phase())
        return RunResult(
            0,
            self.phase,
            StopReason.TRIGGER_HIT if hits else StopReason.EDGE_BARRIER,
            hits,
            True,
        )

    @property
    def watcher_count(self) -> int:
        return len(self._watchers) + len(self._coverage_collectors)

    @property
    def has_coverage_drive_work(self) -> bool:
        return any(c.phase is XPhase.DRIVE_STABLE for c in self._coverage_collectors)

    def _evaluate_phase(self) -> list[BackendHit]:
        for collector in self._coverage_collectors:
            collector.observe(self.phase, self.tick)
        hits: list[BackendHit] = []
        occurrence_ids: dict[tuple[Any, ...], int] = {}
        for slot, watcher in tuple(self._watchers.items()):
            matched = self._match(watcher)
            if matched is None:
                continue
            kind, source, value, terminal = matched
            occurrence_key = (
                kind,
                id(watcher.trigger)
                if kind
                not in (
                    XEventKind.CLOCK_RISE,
                    XEventKind.CLOCK_FALL,
                    XEventKind.DRIVE_STABLE,
                )
                else id(source),
            )
            event_id = occurrence_ids.get(occurrence_key)
            if event_id is None:
                event_id = self._next_event_id
                self._next_event_id += 1
                occurrence_ids[occurrence_key] = event_id
            hits.append(
                BackendHit(
                    event_id=event_id,
                    tick=self.tick,
                    slot=slot,
                    generation=watcher.generation,
                    kind=kind,
                    phase=self.phase,
                    source=source,
                    value=value,
                    terminal_state=terminal,
                )
            )
        return hits

    def _match(
        self, watcher: _Watcher
    ) -> tuple[XEventKind, Any, Any, str | None] | None:
        trigger = watcher.trigger
        if isinstance(trigger, RisingEdge):
            if self.phase is XPhase.RISING_STABLE and _same_source(
                trigger.source, self.clock
            ):
                return XEventKind.CLOCK_RISE, trigger.source, None, None
            return None
        if isinstance(trigger, FallingEdge):
            if self.phase is XPhase.FALLING_STABLE and _same_source(
                trigger.source, self.clock
            ):
                return XEventKind.CLOCK_FALL, trigger.source, None, None
            return None
        if isinstance(trigger, DriveStable):
            if self.phase is XPhase.DRIVE_STABLE and _same_source(
                trigger.source, self.clock
            ):
                return XEventKind.DRIVE_STABLE, trigger.source, None, None
            return None
        if isinstance(trigger, ClockCycles):
            if self.phase is not XPhase.RISING_STABLE or not _same_source(
                trigger.source, self.clock
            ):
                return None
            assert watcher.remaining is not None
            watcher.remaining -= 1
            if watcher.remaining == 0:
                return XEventKind.CLOCK_CYCLES, trigger.source, trigger.cycles, None
            return None
        if isinstance(trigger, SimTimeout):
            if self.phase is not XPhase.RISING_STABLE or not _same_source(
                trigger.clock, self.clock
            ):
                return None
            assert watcher.remaining is not None
            watcher.remaining -= 1
            if watcher.remaining == 0:
                return XEventKind.TIMEOUT, trigger.clock, trigger.cycles, None
            return None
        if isinstance(trigger, Value):
            if not _sample_matches(trigger.sample, self.phase, self.clock):
                return None
            value = getattr(trigger.signal, "value", trigger.signal)
            current = value == trigger.expected
            if _condition_emits(
                trigger.mode, current, watcher.last_condition
            ):
                watcher.last_condition = current
                return XEventKind.VALUE, trigger.signal, value, None
            watcher.last_condition = current
            return None
        if isinstance(trigger, ValueChange):
            if not _sample_matches(trigger.sample, self.phase, self.clock):
                return None
            value = getattr(trigger.signal, "value", trigger.signal)
            if value != watcher.last_value:
                watcher.last_value = value
                return XEventKind.VALUE_CHANGE, trigger.signal, value, None
            return None
        if isinstance(trigger, PythonPredicateTrigger):
            if _sample_matches(trigger.sample, self.phase, self.clock):
                current = bool(trigger.predicate())
                emit = _condition_emits(
                    trigger.mode, current, watcher.last_condition
                )
                watcher.last_condition = current
                if emit:
                    return XEventKind.CONDITION, trigger.name, current, None
            return None
        if isinstance(trigger, CompiledTrigger):
            if not _sample_matches(trigger.sample, self.phase, self.clock):
                return None
            if isinstance(trigger.program, SequenceSpec):
                if self._advance_sequence(trigger, watcher.sequence):
                    return XEventKind.FSM, trigger.name, True, "MATCHED"
                return None
            if isinstance(trigger.program, FsmSpec):
                terminal = self._advance_fsm(trigger, watcher.fsm)
                if terminal is not None:
                    return XEventKind.FSM, trigger.name, True, terminal
                return None
            if not _condition_known(trigger.program, trigger.dut):
                return None
            current = bool(trigger.program.evaluate(trigger.dut))
            emit = _condition_emits(
                trigger.mode, current, watcher.last_condition
            )
            watcher.last_condition = current
            if emit:
                return XEventKind.CONDITION, trigger.name, current, None
            return None
        raise TypeError(f"MemoryBackend does not support {type(trigger).__name__}")

    def _advance_sequence(
        self, trigger: CompiledTrigger, state: _SequenceState
    ) -> bool:
        program = trigger.program
        assert isinstance(program, SequenceSpec)
        return _advance_sequence(program.steps, state,
                                 lambda expr: _evaluate_condition(expr, trigger.dut))

    def _advance_fsm(
        self, trigger: CompiledTrigger, runtime: _FsmState
    ) -> str | None:
        program = trigger.program
        assert isinstance(program, FsmSpec)
        if runtime.current is None:
            runtime.current = program.start
        states = dict(program.states)
        for transition in states[runtime.current].transitions:
            if transition.condition is not None and not _evaluate_condition(transition.condition, trigger.dut):
                continue
            if transition.terminal is not None:
                runtime.current = program.start
                return transition.terminal
            assert transition.target is not None
            runtime.current = transition.target
            break
        return None


class XCommClockBackend:
    """C++ trigger-engine backend for a real ``xspcomm.XClock``.

    The hot path (clock stepping, phase sampling and hit collection) stays in
    xcomm.  Python is re-entered only when ``RunUntil`` returns.  It supports
    edge/cycle/value/change watchers, compiled Expr/Sequence/FSM,
    explicit Python sampling, batched hits and wall-clock run budgets.
    """

    capabilities = BackendCapabilities(
        half_step=True,
        stable_sample=True,
        drive_stable=True,
        reentrant=False,
    )

    def __init__(
        self,
        clock: Any,
        *,
        on_phase: Callable[[XPhase, int], None] | None = None,
        capacity: int = 1024,
        program_cache_capacity: int = 256,
    ) -> None:
        required = ("StepHalf", "GetHalfTick", "GetPhase")
        missing = [name for name in required if not hasattr(clock, name)]
        if missing:
            raise TypeError(
                "xcomm clock is missing half-step API: " + ", ".join(missing)
            )
        if on_phase is not None:
            raise TypeError(
                "XCommClockBackend does not support Python on_phase hooks; "
                "install an XClock callback so it runs before native sampling"
            )
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if program_cache_capacity < 0:
            raise ValueError("program_cache_capacity must be nonnegative")
        try:
            xspcomm = importlib.import_module(type(clock).__module__)
        except ImportError as error:
            raise RuntimeError(
                "XCommClockBackend requires xspcomm with XTriggerEngine"
            ) from error
        if not hasattr(xspcomm, "XTriggerEngine"):
            raise RuntimeError(
                "xspcomm was built without the XTriggerEngine API"
            )

        self.clock = clock
        self._xspcomm = xspcomm
        self._engine = xspcomm.XTriggerEngine(clock, capacity)
        if not hasattr(self._engine, "AcquireClock"):
            raise RuntimeError("xspcomm was built without XEngine clock ownership support; rebuild xcomm")
        self._native_handles: dict[tuple[int, int], Any] = {}
        self._triggers: dict[tuple[int, int], XTrigger[Any]] = {}
        self._program_cache: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self._program_cache_capacity = program_cache_capacity
        # Cached native roots borrow XData pointers: keep their bindings alive.
        self._program_cache_sources: dict[tuple[Any, ...], CompiledTrigger] = {}
        self._compile_depth = 0
        self._collection_pending = False
        self._transaction_new_keys: list[tuple[Any, ...]] = []
        self._collection_threshold = 256
        self._python_last_condition: dict[tuple[int, int], bool] = {}
        self._coverage_collectors: list[Any] = []
        self.tick = int(clock.GetHalfTick())
        self.phase = _xcomm_phase(clock.GetPhase())
        self._closed = False
        self._owner: object | None = None

    def acquire(self, owner: object) -> None:
        if self._closed:
            raise RuntimeError("backend is closed")
        if self._owner is not None and self._owner is not owner:
            raise RuntimeError("backend instance already has an active Execution")
        if self._owner is None and self._native_handles:
            raise RuntimeError("disarm standalone registrations before entering Execution")
        self._engine.AcquireClock()
        self.tick = int(self.clock.GetHalfTick())
        self.phase = _xcomm_phase(self.clock.GetPhase())
        self._owner = owner

    def release(self, owner: object) -> None:
        if self._owner is owner:
            self._engine.ReleaseClock()
            self._owner = None

    def arm(self, trigger: XTrigger[Any]) -> BackendHandle:
        if isinstance(trigger, CompiledTrigger):
            with self._expression_transaction():
                return self._arm(trigger)
        return self._arm(trigger)

    def _arm(self, trigger: XTrigger[Any]) -> BackendHandle:
        if self._closed:
            raise RuntimeError("backend is closed")
        # Standalone backend calls hold the same native lease until close().
        # Execution takes it eagerly in acquire(), before registering work.
        self._engine.AcquireClock()
        cache_key = None
        native_phase: Any
        if isinstance(trigger, RisingEdge):
            self._require_clock_source(trigger.source)
            native = self._engine.ArmEdge(
                self._xspcomm.XPhase_RisingStable
            )
        elif isinstance(trigger, FallingEdge):
            self._require_clock_source(trigger.source)
            native = self._engine.ArmEdge(
                self._xspcomm.XPhase_FallingStable
            )
        elif isinstance(trigger, DriveStable):
            self._require_clock_source(trigger.source)
            native = self._engine.ArmEdge(
                self._xspcomm.XPhase_DriveStable
            )
        elif isinstance(trigger, ClockCycles):
            self._require_clock_source(trigger.source)
            native = self._engine.ArmClockCycles(
                trigger.cycles, self._xspcomm.XPhase_RisingStable
            )
        elif isinstance(trigger, SimTimeout):
            self._require_clock_source(trigger.clock)
            native = self._engine.ArmClockCycles(
                trigger.cycles, self._xspcomm.XPhase_RisingStable
            )
        elif isinstance(trigger, Value):
            native_phase = self._native_sample_phase(trigger.sample)
            if not isinstance(trigger.expected, int):
                raise TypeError(
                    "native Value currently requires an integer expected value"
                )
            native_signal = self._native_xdata(
                trigger.signal, "Value.signal"
            )
            width = self._native_signal_width(native_signal, "Value.signal")
            if trigger.expected < 0 or trigger.expected >= (1 << width):
                raise ValueError(
                    f"Value expected={trigger.expected} does not fit "
                    f"unsigned {width}-bit signal"
                )
            if width > 64:
                native = self._engine.ArmValueEqBytes(
                    native_signal,
                    trigger.expected.to_bytes((width + 7) // 8, "little"),
                    native_phase,
                    self._native_condition_mode(trigger.mode),
                    0,
                )
            else:
                native = self._engine.ArmValueEq(
                    native_signal,
                    trigger.expected,
                    native_phase,
                    self._native_condition_mode(trigger.mode),
                )
        elif isinstance(trigger, ValueChange):
            native_phase = self._native_sample_phase(trigger.sample)
            native_signal = self._native_xdata(
                trigger.signal, "ValueChange.signal"
            )
            self._native_signal_width(native_signal, "ValueChange.signal")
            native = self._engine.ArmValueChange(
                native_signal, native_phase
            )
        elif isinstance(trigger, CompiledTrigger):
            native_phase = self._native_sample_phase(trigger.sample)
            cache_key = self._program_key(
                trigger.program, trigger.dut, int(native_phase)
            )
            compiled = self._program_cache.get(cache_key)
            if compiled is None:
                compiled = self._lower_program(trigger.program, trigger.dut)
            if compiled.kind == self._xspcomm.XTriggerProgramKind_Fsm:
                native = self._engine.ArmFsm(compiled.state_count, compiled.start_state,
                                            compiled.transitions, native_phase)
            elif compiled.kind == self._xspcomm.XTriggerProgramKind_Sequence:
                native = self._engine.ArmSequence(compiled.steps, native_phase)
            else:
                native = self._engine.ArmExpr(compiled.root, native_phase,
                                             self._native_condition_mode(trigger.mode))
        elif isinstance(trigger, PythonPredicateTrigger):
            native_phase = self._native_sample_phase(trigger.sample)
            native = self._engine.ArmSample(native_phase)
        else:
            raise TypeError(
                "native xcomm backend does not yet lower "
                f"{type(trigger).__name__}; use MemoryBackend while developing "
                "the expression/FSM or add its C++ lowering"
            )
        if not native.IsValid():
            raise RuntimeError("xcomm trigger capacity exhausted")
        handle = BackendHandle(int(native.slot), int(native.generation))
        key = (handle.slot, handle.generation)
        self._native_handles[key] = native
        self._triggers[key] = trigger
        if cache_key is not None and self._program_cache_capacity:
            if self._compile_depth and cache_key not in self._program_cache:
                self._transaction_new_keys.append(cache_key)
            self._program_cache[cache_key] = compiled
            self._program_cache.move_to_end(cache_key)
            self._program_cache_sources[cache_key] = trigger
            while len(self._program_cache) > self._program_cache_capacity:
                evicted, _ = self._program_cache.popitem(last=False)
                del self._program_cache_sources[evicted]
        if isinstance(trigger, PythonPredicateTrigger):
            self._python_last_condition[key] = False
        return handle

    def disarm(self, handle: BackendHandle) -> bool:
        key = (handle.slot, handle.generation)
        native = self._native_handles.pop(key, None)
        self._triggers.pop(key, None)
        self._python_last_condition.pop(key, None)
        if native is None:
            return False
        result = bool(self._engine.Disarm(native))
        self._collect_expressions()
        return result

    @contextmanager
    def _expression_transaction(self):
        if not self._compile_depth:
            self._transaction_new_keys.clear()
        self._compile_depth += 1
        try:
            yield
        except BaseException:
            self._collection_pending = True
            raise
        finally:
            self._compile_depth -= 1
            if not self._compile_depth:
                force = self._collection_pending
                if force:
                    for key in self._transaction_new_keys:
                        self._program_cache.pop(key, None)
                        self._program_cache_sources.pop(key, None)
                self._transaction_new_keys.clear()
                self._collection_pending = False
                self._collect_expressions(force=force)

    def _collect_expressions(self, *, force: bool = False) -> None:
        if self._compile_depth or not hasattr(self._engine, "CollectExpressions"):
            return
        if not force and self._engine.ExpressionNodeCount() < self._collection_threshold:
            return
        roots = []
        for program in self._program_cache.values():
            roots.append(int(program.root))
            roots.extend(int(part.root) for part in program.steps)
            roots.extend(int(part.root) for part in program.transitions)
        mapping = self._engine.CollectExpressions(self._xspcomm.XIntVector(roots))
        for program in self._program_cache.values():
            if program.root >= 0:
                program.root = int(mapping[program.root])
            for parts in (program.steps, program.transitions):
                for index in range(len(parts)):
                    part = parts[index]
                    if part.root >= 0:
                        part.root = int(mapping[part.root])
                    parts[index] = part
        self._collection_threshold = max(256, int(self._engine.ExpressionNodeCount()) * 2)

    def collect_programs(self) -> None:
        """Collect unused programs at a stable, non-compiling runtime boundary."""
        if self._closed or self._compile_depth:
            raise RuntimeError("program collection requires an open, non-compiling backend")
        if not hasattr(self._engine, "CollectExpressions"):
            raise NotImplementedError("xcomm lacks expression collection; rebuild the draft native branch")
        self._collect_expressions(force=True)

    def rearm(self, handle: BackendHandle) -> bool:
        native = self._native_handles.get(
            (handle.slot, handle.generation)
        )
        if native is None:
            return False
        return bool(self._engine.Rearm(native))

    def run_until(self, limit: RunLimit) -> RunResult:
        if self._closed:
            raise RuntimeError("backend is closed")
        self._engine.AcquireClock()
        wall_time_ns = (
            0
            if limit.max_wall_time_ms is None
            else max(1, int(limit.max_wall_time_ms * 1_000_000))
        )
        try:
            native_result = self._engine.RunUntil(
                limit.max_ticks,
                wall_time_ns,
                limit.budget_check_interval,
            )
        except RuntimeError as error:
            self._coverage_error(error)
            raise
        return self._convert_native_result(native_result)

    def sample_drive_stable(self) -> RunResult | None:
        if not any(
            _trigger_uses_drive_stable(trigger)
            for trigger in self._triggers.values()
        ):
            return None
        if not hasattr(self._engine, "SamplePhase"):
            raise RuntimeError(
                "xspcomm was built without DriveStable SamplePhase support"
            )
        try:
            native_result = self._engine.SamplePhase(self._xspcomm.XPhase_DriveStable)
        except RuntimeError as error:
            self._coverage_error(error)
            raise
        return self._convert_native_result(native_result)

    def _coverage_error(self, error: RuntimeError) -> None:
        if str(error) != "coverage illegal bin":
            return
        from .coverage import IllegalBinError
        hits = []
        for collector in self._coverage_collectors:
            previous = len(collector.group._illegal_hits)
            collector.sync()
            hits.extend(collector.group._illegal_hits[previous:])
        if hits:
            raise IllegalBinError(hits) from error

    def _convert_native_result(self, native_result: Any) -> RunResult:
        self.tick = int(self.clock.GetHalfTick())
        self.phase = _xcomm_phase(native_result.stopped_phase)
        self._raise_callback_exception()
        converted: list[BackendHit] = []
        sampled_only = False
        for hit in native_result.hits:
            key = (int(hit.slot), int(hit.generation))
            collector = next((c for c in self._coverage_collectors if c.handle is not None
                              and (c.handle.slot, c.handle.generation) == key), None)
            if collector is not None:
                collector.observe(self.phase, self.tick)
                self.rearm(collector.handle)
                sampled_only = True
                continue
            trigger = self._triggers.get(key)
            if isinstance(trigger, PythonPredicateTrigger):
                current = bool(trigger.predicate())
                previous = self._python_last_condition[key]
                self._python_last_condition[key] = current
                if _condition_emits(trigger.mode, current, previous):
                    converted.append(
                        replace(self._convert_hit(hit), value=current)
                    )
                else:
                    native = self._native_handles[key]
                    if not self._engine.RearmSample(native):
                        raise RuntimeError(
                            "xcomm failed to rearm Python sampling watcher"
                        )
                    sampled_only = True
            else:
                converted.append(self._convert_hit(hit))
        hits = tuple(converted)
        stop_reason = _xcomm_stop_reason(native_result.stop_reason)
        if sampled_only and not hits:
            stop_reason = StopReason.PYTHON_SAMPLE
        return RunResult(
            int(native_result.advanced_ticks),
            self.phase,
            stop_reason,
            hits,
            bool(native_result.IsPhaseBarrier()),
        )

    def clear_execution_state(self) -> None:
        """Clear per-Execution native state without closing this backend."""

        if self._native_handles or self._engine.ActiveCount() or self._coverage_collectors:
            raise RuntimeError(
                "cannot reset backend runtime with active watchers"
            )
        self._engine.ClearExecutionState()
        self._native_handles.clear()
        self._triggers.clear()
        self._program_cache.clear()
        self._program_cache_sources.clear()
        self._collection_threshold = 256
        self._python_last_condition.clear()
        self.tick = int(self.clock.GetHalfTick())
        self.phase = _xcomm_phase(self.clock.GetPhase())

    def close(self) -> None:
        if self._closed:
            return
        if self._owner is not None or self._coverage_collectors:
            raise RuntimeError("release the owning Execution before closing its backend")
        self._engine.Clear()
        self._engine.ReleaseClock()
        self._native_handles.clear()
        self._triggers.clear()
        self._program_cache.clear()
        self._program_cache_sources.clear()
        self._python_last_condition.clear()
        self._closed = True

    def refresh_comb(self) -> None:
        """Propagate writes made at the current stable phase."""

        self.clock.RefreshComb()

    @property
    def has_coverage_drive_work(self) -> bool:
        return any(c.phase is XPhase.DRIVE_STABLE for c in self._coverage_collectors)

    @property
    def watcher_count(self) -> int:
        return int(self._engine.ActiveCount())

    @property
    def program_cache_size(self) -> int:
        return len(self._program_cache)

    def _raise_callback_exception(self) -> None:
        errors = getattr(self.clock, "exceptions", None)
        if errors:
            raise errors[0]

    def _require_clock_source(self, source: Any) -> None:
        if _same_source(source, self.clock):
            return
        try:
            native_signal = self._native_xdata(
                source, "edge trigger source"
            )
        except TypeError as error:
            raise ValueError(
                "trigger source is neither this backend's XClock nor one "
                "of its registered clock pins"
            ) from error
        if not hasattr(self.clock, "HasClockPin"):
            raise RuntimeError(
                "xspcomm was built without XClock.HasClockPin; rebuild "
                "xspcomm to await generated DUT clock pins safely"
            )
        if not self.clock.HasClockPin(native_signal):
            raise ValueError(
                "trigger source is not a registered clock pin of this "
                "backend's XClock"
            )

    def _native_sample_phase(self, sample: Any) -> Any:
        self._require_clock_source(sample.source)
        if isinstance(sample, RisingEdge):
            return self._xspcomm.XPhase_RisingStable
        if isinstance(sample, FallingEdge):
            return self._xspcomm.XPhase_FallingStable
        if isinstance(sample, DriveStable):
            return self._xspcomm.XPhase_DriveStable
        raise TypeError(
            "sample must be RisingEdge, FallingEdge, or DriveStable"
        )

    def _native_condition_mode(self, mode: ConditionMode | str) -> Any:
        return {
            ConditionMode.ENTER: self._xspcomm.XConditionMode_Enter,
            ConditionMode.EACH_SAMPLE:
                self._xspcomm.XConditionMode_EachSample,
            ConditionMode.CHANGE: self._xspcomm.XConditionMode_Change,
        }[ConditionMode(mode)]

    def _native_xdata(self, signal: Any, label: str) -> Any:
        """Return the direct XData consumed by the native trigger ABI."""
        if isinstance(signal, self._xspcomm.XData):
            return signal
        raise TypeError(f"{label} must be an xspcomm.XData")

    @staticmethod
    def _native_signal_width(signal: Any, label: str) -> int:
        try:
            width = int(signal.W())
        except (AttributeError, TypeError) as error:
            raise TypeError(f"{label} must be an xspcomm.XData") from error
        width = 1 if width == 0 else width
        return width

    def _direct_native_signal(
        self, expression: XExpr, dut: Any
    ) -> tuple[Any, int] | None:
        if isinstance(expression, SignalExpr):
            path = ".".join(expression.path)
            signal = self._native_xdata(
                resolve_path(dut, expression.path),
                f"@xtrigger signal '{path}'",
            )
            return signal, self._native_signal_width(
                signal, f"@xtrigger signal '{path}'"
            )
        if isinstance(expression, BoundSignalExpr):
            signal = self._native_xdata(
                expression.signal, "bound expression signal"
            )
            return signal, self._native_signal_width(
                signal, "bound expression signal"
            )
        return None

    @staticmethod
    def _constant_int(expression: XExpr) -> int | None:
        if not isinstance(expression, ConstantExpr) or not isinstance(
            expression.value, (bool, int)
        ):
            return None
        return int(expression.value)

    def _lower_wide_compare(
        self, expression: BinaryExpr, dut: Any, native_op: Any
    ) -> int | None:
        left_signal = self._direct_native_signal(expression.left, dut)
        right_signal = self._direct_native_signal(expression.right, dut)
        if not (
            (left_signal is not None and left_signal[1] > 64)
            or (right_signal is not None and right_signal[1] > 64)
        ):
            return None
        if left_signal is not None and right_signal is not None:
            if left_signal[1] != right_signal[1]:
                raise ValueError(
                    "wide @xtrigger signal comparison requires equal widths: "
                    f"{left_signal[1]} != {right_signal[1]}"
                )
            return int(
                self._engine.ExprNewCompareSigSig(
                    native_op, left_signal[0], right_signal[0]
                )
            )
        if left_signal is not None:
            constant = self._constant_int(expression.right)
            signal, width = left_signal
            reverse = False
        elif right_signal is not None:
            constant = self._constant_int(expression.left)
            signal, width = right_signal
            reverse = True
        else:
            return None
        if constant is None:
            raise TypeError(
                "wide @xtrigger comparison supports a direct signal and "
                "an integer constant, or two direct signals"
            )
        if constant < 0 or constant >= (1 << width):
            raise ValueError(
                f"@xtrigger constant {constant} does not fit unsigned "
                f"{width}-bit signal"
            )
        encoded = constant.to_bytes((width + 7) // 8, "little")
        if reverse:
            return int(
                self._engine.ExprNewCompareConstBytesSig(
                    native_op, encoded, signal
                )
            )
        return int(
            self._engine.ExprNewCompareSigConstBytes(
                native_op, signal, encoded
            )
        )

    def _lower_expr(self, expression: XExpr, dut: Any) -> int:
        if _uses_typed(expression):
            if not hasattr(self._engine, "ExprNewBits"):
                raise NotImplementedError("typed expressions require the draft xcomm Bits API; rebuild xcomm")
            kind = expression_type(expression, dut)
            root = self._lower_typed_expr(expression, dut)
            if isinstance(kind, BitsType):
                root = int(self._engine.ExprNewBits(self._xspcomm.ExprOp_LNOT, root, -1, 1, False))
                root = int(self._engine.ExprNewBits(self._xspcomm.ExprOp_LNOT, root, -1, 1, False))
            return root
        if isinstance(expression, ConstantExpr):
            if not isinstance(expression.value, (bool, int)):
                raise TypeError(
                    "@xtrigger native constants must be bool or int, got "
                    f"{type(expression.value).__name__}"
                )
            value = int(expression.value)
            if value < 0:
                raise ValueError(
                    "@xtrigger native negative constants require explicit "
                    "width/sign support"
                )
            if value > (1 << 64) - 1:
                raise ValueError("@xtrigger constants must fit uint64")
            return int(self._engine.ExprNewConst(value))
        if isinstance(expression, SignalExpr):
            signal = resolve_path(dut, expression.path)
            path = ".".join(expression.path)
            native_signal = self._native_xdata(
                signal, f"@xtrigger signal '{path}'"
            )
            self._native_signal_width(
                native_signal, f"@xtrigger signal '{path}'"
            )
            if int(native_signal.W()) > 64:
                raise ValueError(
                    "wide @xtrigger signals must appear directly in a "
                    "comparison"
                )
            try:
                root = int(self._engine.ExprNewSignal(native_signal))
            except TypeError as error:
                raise TypeError(
                    f"@xtrigger signal '{path}' is not an xspcomm.XData"
                ) from error
            if root < 0:
                raise ValueError("@xtrigger signal resolved to null")
            return root
        if isinstance(expression, BoundSignalExpr):
            native_signal = self._native_xdata(
                expression.signal, "bound expression signal"
            )
            self._native_signal_width(
                native_signal, "bound expression signal"
            )
            if int(native_signal.W()) > 64:
                raise ValueError(
                    "wide bound signals must appear directly in a comparison"
                )
            root = int(self._engine.ExprNewSignal(native_signal))
            if root < 0:
                raise ValueError("bound expression signal resolved to null")
            return root
        if isinstance(expression, UnaryExpr):
            if expression.op != "not":
                raise TypeError(
                    f"unsupported @xtrigger unary op: {expression.op}"
                )
            child = self._lower_expr(expression.operand, dut)
            return int(
                self._engine.ExprNewUnary(
                    self._xspcomm.ExprOp_LNOT, child
                )
            )
        if isinstance(expression, BinaryExpr):
            binary_ops = {
                "and": self._xspcomm.ExprOp_LAND,
                "or": self._xspcomm.ExprOp_LOR,
            }
            compare_ops = {
                "eq": self._xspcomm.ExprOp_EQ,
                "ne": self._xspcomm.ExprOp_NE,
                "lt": self._xspcomm.ExprOp_LT,
                "le": self._xspcomm.ExprOp_LE,
                "gt": self._xspcomm.ExprOp_GT,
                "ge": self._xspcomm.ExprOp_GE,
            }
            if expression.op in compare_ops:
                wide = self._lower_wide_compare(
                    expression, dut, compare_ops[expression.op]
                )
                if wide is not None:
                    return wide
            left = self._lower_expr(expression.left, dut)
            right = self._lower_expr(expression.right, dut)
            if expression.op in binary_ops:
                return int(
                    self._engine.ExprNewBinary(
                        binary_ops[expression.op], left, right
                    )
                )
            if expression.op in compare_ops:
                return int(
                    self._engine.ExprNewCompare(
                        compare_ops[expression.op], left, right
                    )
                )
            raise TypeError(
                f"unsupported @xtrigger binary op: {expression.op}"
            )
        raise TypeError(
            "unsupported @xtrigger IR node: "
            f"{type(expression).__name__}"
        )

    def _lower_typed_expr(self, expression: XExpr, dut: Any, context: BitsType | None = None) -> int:
        kind = expression_type(expression, dut, context)
        width, signed = (kind.width, kind.signed) if isinstance(kind, BitsType) else (1, False)
        if isinstance(expression, (ConstantExpr, BitsConstExpr)):
            if isinstance(kind, BoolType):
                return int(self._engine.ExprNewBool(bool(expression.value)))
            encoded = (int(expression.value) & ((1 << width) - 1)).to_bytes((width + 7) // 8, "little")
            return int(self._engine.ExprNewBitsConst(width, signed, encoded))
        if isinstance(expression, (SignalExpr, BoundSignalExpr)):
            signal = resolve_path(dut, expression.path) if isinstance(expression, SignalExpr) else expression.signal
            return int(self._engine.ExprNewBitsSignal(self._native_xdata(signal, "typed expression signal"), signed))
        if isinstance(expression, CastExpr):
            lhs = self._lower_typed_expr(expression.operand, dut)
            return int(self._engine.ExprNewBits(self._xspcomm.ExprOp_CAST, lhs, -1, width, signed))
        operations = {"not": "LNOT", "bit_not": "BNOT", "neg": "SUB", "and": "LAND", "or": "LOR",
                      "add": "ADD", "sub": "SUB", "mul": "MUL", "div": "DIV", "mod": "MOD",
                      "bit_and": "BAND", "bit_or": "BOR", "bit_xor": "BXOR", "shl": "SHL", "shr": "SHR",
                      "eq": "EQ", "ne": "NE", "lt": "LT", "le": "LE", "gt": "GT", "ge": "GE"}
        if isinstance(expression, UnaryExpr):
            lhs, rhs = self._lower_typed_expr(expression.operand, dut), -1
        elif isinstance(expression, BinaryExpr):
            a, b = _operand_types(expression, dut)
            lhs = self._lower_typed_expr(expression.left, dut, a if isinstance(a, BitsType) else None)
            rhs = self._lower_typed_expr(expression.right, dut, b if isinstance(b, BitsType) else None)
        else:
            raise TypeError(f"unsupported typed expression {type(expression).__name__}")
        return int(self._engine.ExprNewBits(getattr(self._xspcomm, "ExprOp_" + operations[expression.op]),
                                          lhs, rhs, width, signed))

    def _program_key(
        self, program: XExpr | SequenceSpec | FsmSpec, dut: Any, phase: int
    ) -> tuple[Any, ...]:
        if isinstance(program, FsmSpec):
            states = []
            for name, state in program.states:
                transitions = []
                for transition in state.transitions:
                    condition = (
                        None
                        if transition.condition is None
                        else self._expr_key(transition.condition, dut)
                    )
                    transitions.append(
                        (condition, transition.target, transition.terminal)
                    )
                states.append((name, tuple(transitions)))
            return ("fsm", phase, program.start, tuple(states))
        if isinstance(program, SequenceSpec):
            steps = []
            for step in program.steps:
                if isinstance(step, WaitStep):
                    shape = ("wait",)
                elif isinstance(step, NextStep):
                    shape = ("next",)
                elif isinstance(step, WithinStep):
                    shape = ("within", step.minimum, step.maximum)
                elif isinstance(step, HoldStep):
                    shape = ("hold", step.cycles)
                else:
                    raise TypeError(
                        f"unsupported Sequence step: {type(step).__name__}"
                    )
                steps.append(shape + (self._expr_key(step.condition, dut),))
            return ("sequence", phase, tuple(steps))
        return ("expr", phase, self._expr_key(program, dut))

    def _expr_key(self, expression: XExpr, dut: Any) -> tuple[Any, ...]:
        if isinstance(expression, BitsConstExpr):
            return ("bits-constant", expression.value, expression.width, expression.signed)
        if isinstance(expression, CastExpr):
            return ("cast", expression.width, expression.signed, self._expr_key(expression.operand, dut))
        if isinstance(expression, ConstantExpr):
            value = expression.value
            if not isinstance(value, (bool, int)):
                raise TypeError(
                    "@xtrigger native constants must be bool or int, got "
                    f"{type(value).__name__}"
                )
            return ("const", type(value).__name__, int(value))
        if isinstance(expression, SignalExpr):
            signal = resolve_path(dut, expression.path)
            from .signals import signal_width
            return ("signal", expression.path, id(signal), signal_width(signal))
        if isinstance(expression, BoundSignalExpr):
            native_signal = self._native_xdata(
                expression.signal, "bound expression signal"
            )
            return ("bound-signal", signal_identity(native_signal), int(native_signal.W()) or 1)
        if isinstance(expression, UnaryExpr):
            return (
                "unary",
                expression.op,
                self._expr_key(expression.operand, dut),
            )
        if isinstance(expression, BinaryExpr):
            return (
                "binary",
                expression.op,
                self._expr_key(expression.left, dut),
                self._expr_key(expression.right, dut),
            )
        raise TypeError(
            "unsupported @xtrigger IR node: "
            f"{type(expression).__name__}"
        )

    def _lower_program(self, program: Any, dut: Any) -> Any:
        """One native program compiler for ordinary waits and coverage bins."""
        native = self._xspcomm.XTriggerProgram()
        native.overlap = False
        if isinstance(program, FsmSpec):
            native.kind = self._xspcomm.XTriggerProgramKind_Fsm
            native.state_count, native.start_state, native.transitions = self._lower_fsm(program, dut)
        elif isinstance(program, SequenceSpec):
            native.kind = self._xspcomm.XTriggerProgramKind_Sequence
            native.steps = self._lower_sequence(program, dut)
        else:
            native.root = self._lower_expr(program, dut)
        return native

    def _lower_sequence(self, sequence: SequenceSpec, dut: Any) -> Any:
        native_steps = self._xspcomm.XSequenceStepVector()
        for step in sequence.steps:
            native = self._xspcomm.XSequenceStep()
            native.root = self._lower_expr(step.condition, dut)
            if isinstance(step, WaitStep):
                native.kind = self._xspcomm.XSequenceStepKind_Wait
            elif isinstance(step, NextStep):
                native.kind = self._xspcomm.XSequenceStepKind_Next
            elif isinstance(step, WithinStep):
                native.kind = self._xspcomm.XSequenceStepKind_Within
                native.minimum = step.minimum
                native.maximum = step.maximum
            elif isinstance(step, HoldStep):
                native.kind = self._xspcomm.XSequenceStepKind_Hold
                native.cycles = step.cycles
            else:
                raise TypeError(
                    "unsupported Sequence step: "
                    f"{type(step).__name__}"
                )
            native_steps.push_back(native)
        return native_steps

    def _lower_fsm(self, program: FsmSpec, dut: Any) -> Any:
        state_ids = {
            name: index for index, (name, _) in enumerate(program.states)
        }
        terminal_ids = {
            name: index
            for index, name in enumerate(self._fsm_terminals(program))
        }
        native_transitions = self._xspcomm.XFsmTransitionVector()
        for state_name, state in program.states:
            for transition in state.transitions:
                native = self._xspcomm.XFsmTransition()
                native.from_state = state_ids[state_name]
                native.root = (
                    -1
                    if transition.condition is None
                    else self._lower_expr(transition.condition, dut)
                )
                if transition.terminal is not None:
                    native.trigger = True
                    native.terminal_id = terminal_ids[transition.terminal]
                else:
                    assert transition.target is not None
                    native.next_state = state_ids[transition.target]
                native_transitions.push_back(native)
        return (
            len(state_ids),
            state_ids[program.start],
            native_transitions,
        )

    @staticmethod
    def _fsm_terminals(program: FsmSpec) -> tuple[str, ...]:
        names: list[str] = []
        for _, state in program.states:
            for transition in state.transitions:
                terminal = transition.terminal
                if terminal is not None and terminal not in names:
                    names.append(terminal)
        return tuple(names)

    def _convert_hit(self, hit: Any) -> BackendHit:
        key = (int(hit.slot), int(hit.generation))
        trigger = self._triggers.get(key)
        kind = _xcomm_hit_kind(hit.kind)
        terminal_state: str | None = None
        if isinstance(trigger, Value):
            source = trigger.signal
            value: Any = (
                read_signal(source)
                if self._native_signal_width(source, "Value.signal") > 64
                else int(hit.value)
            )
        elif isinstance(trigger, ValueChange):
            source = trigger.signal
            width = self._native_signal_width(
                trigger.signal, "ValueChange.signal"
            )
            if width > 64:
                sampled = sample_signal(source)
                value = sampled.value if sampled.is_known else sampled
            else:
                x_mask = int(hit.x_mask)
                value = (
                    int(hit.value)
                    if x_mask == 0
                    else LogicValue(
                        value=int(hit.value),
                        x_mask=x_mask,
                        width=width,
                    )
                )
        elif isinstance(trigger, SimTimeout):
            source = trigger.clock
            value = trigger.cycles
            kind = XEventKind.TIMEOUT
        elif isinstance(trigger, CompiledTrigger):
            source = trigger.name
            if kind is XEventKind.FSM:
                value = True
                if isinstance(trigger.program, FsmSpec):
                    terminals = self._fsm_terminals(trigger.program)
                    terminal_id = int(hit.value)
                    if terminal_id >= len(terminals):
                        raise RuntimeError(
                            f"unknown native FSM terminal id: {terminal_id}"
                        )
                    terminal_state = terminals[terminal_id]
            else:
                value = bool(hit.value)
        elif isinstance(trigger, PythonPredicateTrigger):
            source = trigger.name
            value = True
        elif trigger is not None:
            source = trigger.source
            value = (
                trigger.cycles if isinstance(trigger, ClockCycles) else None
            )
        else:
            source = int(hit.source_id)
            value = int(hit.value)
        return BackendHit(
            event_id=int(hit.event_id),
            tick=int(hit.tick),
            slot=key[0],
            generation=key[1],
            kind=kind,
            phase=_xcomm_phase(hit.phase),
            source=source,
            value=value,
            x_mask=int(hit.x_mask),
            terminal_state=terminal_state,
        )


def _sample_matches(sample: Any, phase: XPhase, clock: Any) -> bool:
    if isinstance(sample, RisingEdge):
        return phase is XPhase.RISING_STABLE and _same_source(sample.source, clock)
    if isinstance(sample, FallingEdge):
        return phase is XPhase.FALLING_STABLE and _same_source(sample.source, clock)
    if isinstance(sample, DriveStable):
        return phase is XPhase.DRIVE_STABLE and _same_source(sample.source, clock)
    return False


def _trigger_uses_drive_stable(trigger: XTrigger[Any]) -> bool:
    return isinstance(trigger, DriveStable) or isinstance(
        getattr(trigger, "sample", None), DriveStable
    )


def _condition_emits(
    mode: ConditionMode | str, current: bool, previous: bool
) -> bool:
    mode = ConditionMode(mode)
    if mode is ConditionMode.EACH_SAMPLE:
        return current
    if mode is ConditionMode.ENTER:
        return current and not previous
    return current != previous


def _same_source(left: Any, right: Any) -> bool:
    if left is right:
        return True
    if isinstance(left, (str, int)) and isinstance(right, type(left)):
        return left == right
    return False


def _xcomm_phase(value: Any) -> XPhase:
    # SWIG exposes scoped C++ enums as module-level integer constants.
    try:
        return {
            0: XPhase.FALLING_STABLE,
            1: XPhase.RISING_STABLE,
            2: XPhase.DRIVE_STABLE,
        }[int(value)]
    except KeyError as error:
        raise RuntimeError(f"unknown xcomm phase: {int(value)}") from error


def _xcomm_hit_kind(value: Any) -> XEventKind:
    try:
        return {
            0: XEventKind.CLOCK_FALL,
            1: XEventKind.CLOCK_RISE,
            2: XEventKind.CLOCK_CYCLES,
            3: XEventKind.VALUE,
            4: XEventKind.CONDITION,
            5: XEventKind.FSM,
            6: XEventKind.VALUE_CHANGE,
            7: XEventKind.DRIVE_STABLE,
        }[int(value)]
    except KeyError as error:
        raise RuntimeError(f"unknown xcomm hit kind: {int(value)}") from error


def _xcomm_stop_reason(value: Any) -> StopReason:
    try:
        return {
            0: StopReason.RUN_LIMIT,
            1: StopReason.TRIGGER_HIT,
            2: StopReason.BACKEND_STOP,
            3: StopReason.QUANTUM_EXPIRED,
            4: StopReason.EDGE_BARRIER,
            5: StopReason.USER_PAUSE,
            6: StopReason.SIMULATION_CLOSE,
            7: StopReason.CALLBACK_ERROR,
            8: StopReason.BACKEND_ERROR,
        }[int(value)]
    except KeyError as error:
        raise RuntimeError(f"unknown xcomm stop reason: {int(value)}") from error
