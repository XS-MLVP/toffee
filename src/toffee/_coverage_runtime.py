"""Execution-owned sampling for existing CoverGroup instances.

No task is created per collector. Native registrations count in xcomm; Python
fallbacks use the same backend phase boundary and never create clock demand.
"""
from __future__ import annotations

from dataclasses import fields as dataclass_fields, is_dataclass
from copy import deepcopy
from enum import Enum
from threading import get_ident
from typing import Any

from .backend import MemoryBackend, XCommClockBackend, _Watcher
from .coverage import (BinKind, DefaultMatcher, IllegalHit, RangeMatcher,
                       TransitionMatcher, PatternMatcher, ValueMatcher, WildcardMatcher, _tuple_key, _tuple_label,
                       _DIAGNOSTIC_FIELDS, _empty_pattern_diagnostics, _merge_diagnostics)
from .events import LogicValue, XPhase
from .ir import (BoundSignalExpr, ConstantExpr, Next, Sequence, Wait, XExpr,
                 SequenceSpec, FsmSpec, WaitStep)
from .signals import read_signal, sample_signal
from .triggers import (CompiledTrigger, DriveStable, FallingEdge, PhaseTrigger,
                       PythonPredicateTrigger, RisingEdge)


def configure(trigger, fields, abort, strategy, accumulate, contract,
              overlap, max_active, diagnostics, root=None):
    if not isinstance(trigger, (RisingEdge, FallingEdge, DriveStable,
                                CompiledTrigger, PythonPredicateTrigger)):
        raise TypeError("coverage trigger must be a phase, compiled pattern, or Python predicate")
    if strategy not in ("auto", "native", "python"):
        raise ValueError("coverage strategy must be auto, native, or python")
    if not isinstance(accumulate, bool):
        raise TypeError("coverage accumulate must be bool")
    if contract is not None and (not isinstance(contract, str) or not contract):
        raise ValueError("coverage contract must be a nonempty stable name/version")
    if isinstance(trigger, PythonPredicateTrigger) and contract is None:
        raise ValueError("Python coverage predicates require a stable contract name/version")
    if (not fields and root is None) or any(not isinstance(name, str) or not name for name in fields):
        raise ValueError("coverage fields must be a nonempty mapping with named sources")
    if not isinstance(overlap, bool):
        raise TypeError("coverage overlap must be bool")
    if diagnostics not in ("off", "summary"):
        raise ValueError("coverage diagnostics must be off or summary")
    if max_active is not None and (isinstance(max_active, bool) or not isinstance(max_active, int)
                                   or not 1 <= max_active <= (1 << 32) - 1):
        raise ValueError("max_active must be a positive uint32 integer")
    if overlap:
        if not isinstance(trigger, CompiledTrigger) or not isinstance(trigger.program, (SequenceSpec, FsmSpec)):
            raise ValueError("overlap requires a compiled Sequence or FSM")
        if max_active is None:
            raise ValueError("overlap requires an explicit max_active")
        if isinstance(trigger.program, SequenceSpec) and not isinstance(trigger.program.steps[0], WaitStep):
            raise ValueError("overlapping Sequence must start with Wait")
    elif max_active not in (None, 1):
        raise ValueError("max_active greater than one requires overlap=True")
    return dict(trigger=trigger, fields=dict(fields), abort=abort, strategy=strategy,
                accumulate=accumulate, contract=contract, overlap=overlap,
                max_active=max_active or 1, diagnostics=diagnostics, root=root)


def _shape(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BoundSignalExpr):
        raise ValueError("bound pattern expressions require an explicit contract name/version")
    if is_dataclass(value):
        return {"type": type(value).__name__, **{
            item.name: _shape(getattr(value, item.name)) for item in dataclass_fields(value)}}
    if isinstance(value, (tuple, list)):
        return [_shape(item) for item in value]
    if isinstance(value, (str, int, bool, type(None))):
        return value
    raise ValueError("coverage pattern requires an explicit contract name/version")


def _read(signal):
    if callable(getattr(signal, "XMask", None)):
        value = sample_signal(signal)
        return value.as_int() if value.is_known else value
    value = read_signal(signal)
    return value.as_int() if isinstance(value, LogicValue) and value.is_known else value


class _Collector:
    def __init__(self, group, execution):
        self.group, self.execution, self.backend = group, execution, execution.backend
        self.config = group._binding
        self.trigger = self.config["trigger"]
        if getattr(self.trigger, "sample", None) is None and not isinstance(self.trigger, PhaseTrigger):
            if execution.default_sample is None:
                raise ValueError("coverage requires an explicit sampling phase")
            from dataclasses import replace
            self.trigger = replace(self.trigger, sample=execution.default_sample)
        self.phase_trigger = (self.trigger if isinstance(self.trigger, PhaseTrigger)
                              else self.trigger.sample)
        if self.phase_trigger.source is not self.backend.clock:
            raise ValueError("coverage sampling clock must belong to its Execution backend")
        self.phase = {RisingEdge: XPhase.RISING_STABLE, FallingEdge: XPhase.FALLING_STABLE,
                      DriveStable: XPhase.DRIVE_STABLE}[type(self.phase_trigger)]
        self.thread = get_ident()
        self.handle = None
        self.native = False
        self.reason = None
        self.last = None
        self.epoch = 0
        self.illegal_seen = 0
        self.last_sample = None
        self.failure = None
        self.cross_values = {}
        self.matcher = MemoryBackend(self.backend.clock)
        self.watcher = _Watcher(self.trigger, 0)
        self.pattern = isinstance(self.trigger, CompiledTrigger) and isinstance(
            self.trigger.program, (SequenceSpec, FsmSpec))
        self.attempts = []
        self.bin_patterns, self.pattern_executions = {}, {}
        pooled = {}
        for point in group.definition.points:
            for named in point._named_bins:
                spec = named.spec.matcher
                if isinstance(spec, PatternMatcher):
                    key = (point.name, named.name)
                    identity = (point.name, spec.execution_key())
                    state = pooled.get(identity)
                    if state is None:
                        trigger = CompiledTrigger(named.name, self.config["root"], spec.program,
                                                  self.phase_trigger, spec.mode)
                        state = dict(trigger=trigger, spec=spec, attempts=[],
                                     watcher=_Watcher(trigger, 0), key=key, point=point, bins=[])
                        pooled[identity] = self.pattern_executions[key] = state
                    state["bins"].append(key)
                    self.bin_patterns[key] = state
        self.pattern_keys = ([(None, None)] if self.pattern else []) + [
            (point.name, item.name) for point in group.definition.points for item in point._named_bins
            if isinstance(item.spec.matcher, (TransitionMatcher, PatternMatcher))]
        self.summary = ({key: _empty_pattern_diagnostics() for key in self.pattern_keys}
                        if self.config["diagnostics"] == "summary" else None)
        self.base_diagnostics = None
        self.native_pattern_ids = {}
        self.item_names = [p.name for p in group.definition.points] + [c.name for c in group.definition.crosses]
        self.bin_names = []
        self.normal_bins = {(point.name, item.name)
                            for point in group.definition.points for item in point.normal_bins}
        self.normal_bins.update((cross.definition.name, _tuple_key(names))
                                for cross in group.definition._resolved_crosses for names in cross.eligible)
        self.widths = {name: (int(signal.W()) or 1) if callable(getattr(signal, "W", None))
                       else getattr(signal, "width", None) for name, signal in self.config["fields"].items()}
        program = (self.config["contract"] if self.config["contract"] is not None else
                   _shape(self.trigger.program) if isinstance(self.trigger, CompiledTrigger) else None)
        abort = self.config["abort"]
        abort_name = next((name for name, value in self.config["fields"].items() if value is abort), None)
        if abort is not None and abort_name is None and self.config["contract"] is None:
            raise ValueError("abort must be a named coverage field or have an explicit contract")
        self.contract = dict(phase=self.phase.name, program=program, fields=self.widths,
                             mode=getattr(self.trigger, "mode", None), abort=abort_name,
                             name=self.config["contract"], overlap=self.config["overlap"],
                             max_active=self.config["max_active"])

    def check_thread(self):
        if get_ident() != self.thread:
            raise RuntimeError("live coverage synchronization requires the Execution thread")

    def start(self):
        g = self.group
        required = {point.source or point.name for point in g.definition.points if not point.is_pattern}
        for item in (g.definition, *g.definition.points, *g.definition.crosses):
            if item.iff is not None:
                required.add(item.iff.source)
        missing = required - self.config["fields"].keys()
        if missing:
            raise ValueError(f"unbound coverage fields: {sorted(missing)}")
        if self.config["accumulate"] and g._sampling_contract not in (None, self.contract):
            raise ValueError("cannot accumulate coverage with a different sampling contract")
        if self.config["accumulate"] and g._sampling_contract is None and g.samples:
            raise ValueError("cannot accumulate manual samples into clock-bound coverage")
        try:
            if self.config["strategy"] != "python":
                try:
                    self._start_native()
                except NotImplementedError as error:
                    if self.config["strategy"] == "native":
                        raise
                    self.reason = str(error)
            if not self.native and isinstance(self.backend, XCommClockBackend):
                self.handle = self.backend.arm(self.phase_trigger)
            if not self.config["accumulate"]:
                g.reset()
            g._history.clear()
            g._sampling_contract = self.contract
            g._sampling_backend = "native" if self.native else "python"
            g._sampling_fallback = self.reason
            self.base_diagnostics = deepcopy(g._diagnostics)
            self.publish_diagnostics()
            self.backend._coverage_collectors.append(self)
        except BaseException:
            if self.handle is not None:
                self.backend.disarm(self.handle)
                self.handle = None
            raise

    def _start_native(self):
        if not isinstance(self.backend, XCommClockBackend):
            raise NotImplementedError("backend does not provide native coverage descriptor protocol")
        with self.backend._expression_transaction():
            self._build_native()

    def _build_native(self):
        b = self.backend
        if not isinstance(b, XCommClockBackend) or not hasattr(b._engine, "CoverageVersion"):
            raise NotImplementedError("backend does not provide native coverage descriptor protocol")
        protocol = b._engine.CoverageVersion()
        if protocol != 5:
            raise NotImplementedError("native coverage requires descriptor version 5; rebuild xcomm")
        if isinstance(self.trigger, PythonPredicateTrigger):
            raise NotImplementedError("Python predicates require Python sampling")
        from .ir import PythonPredicateExpr
        if any(isinstance(named.spec.matcher, PatternMatcher) and isinstance(named.spec.matcher.program, PythonPredicateExpr)
               for point in self.group.definition.points for named in point._named_bins):
            raise NotImplementedError("pytrigger bins require Python sampling")
        x, engine = b._xspcomm, b._engine
        sources = self.config["fields"]
        for name, signal in sources.items():
            if not isinstance(signal, x.XData):
                raise NotImplementedError(f"native coverage field {name!r} requires XData")

        def source(name):
            if name not in sources:
                raise ValueError(f"coverage field {name!r} is not bound")
            return BoundSignalExpr(sources[name])

        def lower(expr):
            return b._lower_expr(expr, None)

        def scalar(value):
            if isinstance(value, Enum):
                value = value.value
            if not isinstance(value, (int, bool)) or int(value) < 0:
                raise NotImplementedError("native coverage requires unsigned integer constants")
            return int(value)

        def compare(expr, op, value):
            value = scalar(value)
            width = int(expr.signal.W()) or 1
            # Values outside the observed unsigned domain are unreachable; do not
            # truncate them or let a range endpoint wrap into that domain.
            if value >= 1 << width:
                # Retain the source dependency so an inverted Iff still rejects X/Z.
                return (expr == expr) if op == "le" else (expr != expr)
            return {"eq": expr.__eq__, "ge": expr.__ge__, "le": expr.__le__}[op](value)

        def values(expr, vals):
            result = ConstantExpr(False)
            for value in vals:
                result = result | compare(expr, "eq", value)
            return result

        def gate(iff):
            if iff is None:
                return -1
            expr = values(source(iff.source), iff.values)
            return lower(~expr if iff.invert else expr)

        items, bins = x.XCoveragePointVector(), x.XCoverageBinVector()
        bin_ids, point_ids, lowered_programs = {}, {}, {}
        if self.pattern:
            self.native_pattern_ids[0] = (None, None)
        for point in self.group.definition.points:
            item = x.XCoveragePoint()
            point_ids[point.name] = len(items)
            expr = None if point.is_pattern else source(point.source or point.name)
            if point.is_pattern:
                item.kind = x.XCoveragePointKind_Pattern
            else:
                item.signal = expr.signal
            item.gate = gate(point.iff)
            items.push_back(item)
            for named in point._named_bins:
                spec = x.XCoverageBin()
                spec.point_id = point_ids[point.name]
                spec.kind = list(BinKind).index(named.spec.kind)
                matcher = named.spec.matcher
                if isinstance(matcher, PatternMatcher):
                    self.native_pattern_ids[len(bins) + 1] = (point.name, named.name)
                    program, root = matcher.program, self.config["root"]
                    execution_key = (point.name, matcher.execution_key())
                    if execution_key not in lowered_programs:
                        compiled = b._lower_program(program, root)
                        compiled.mode = b._native_condition_mode(matcher.mode)
                        compiled.overlap, compiled.max_active = matcher.overlap, matcher.max_active
                        lowered_programs[execution_key] = compiled
                    spec.program = lowered_programs[execution_key]
                    if isinstance(program, FsmSpec):
                        names = b._fsm_terminals(program)
                        spec.terminals = x.XUInt32Vector([names.index(name) for name in matcher.terminals])
                elif isinstance(matcher, ValueMatcher):
                    spec.program.root = lower(values(expr, matcher.values))
                elif isinstance(matcher, RangeMatcher):
                    condition = ConstantExpr(False)
                    for lo, hi in matcher.ranges:
                        condition = condition | (compare(expr, "ge", lo) & compare(expr, "le", hi))
                    spec.program.root = lower(condition)
                elif isinstance(matcher, WildcardMatcher):
                    width = int(expr.signal.W()) or 1
                    if matcher.value >= 1 << width:
                        spec.program.root = lower(ConstantExpr(False))
                    else:
                        size = (width + 7) // 8
                        mask = matcher.mask & ((1 << width) - 1)
                        spec.program.root = engine.ExprNewMaskedCompareSigConstBytes(
                            expr.signal, matcher.value.to_bytes(size, "little"),
                            mask.to_bytes(size, "little"))
                elif isinstance(matcher, TransitionMatcher):
                    self.native_pattern_ids[len(bins) + 1] = (point.name, named.name)
                    program = Sequence(Wait(compare(expr, "eq", matcher.values[0])),
                                       *(Next(compare(expr, "eq", value)) for value in matcher.values[1:]))
                    spec.program = b._lower_program(program, None)
                    spec.program.overlap = matcher.overlap
                elif not isinstance(matcher, DefaultMatcher):
                    raise NotImplementedError(f"unsupported native matcher {type(matcher).__name__}")
                bin_ids[(point.name, named.name)] = len(bins)
                bins.push_back(spec)
                self.bin_names.append((point.name, named.name))
        for cross in self.group.definition._resolved_crosses:
            item = x.XCoveragePoint()
            item.kind = x.XCoveragePointKind_Cross
            item.gate = gate(cross.definition.iff)
            item.dimensions = x.XUInt32Vector([point_ids[name] for name in cross.definition.points])
            item_id = len(items)
            items.push_back(item)
            for kind, tuples in ((0, cross.eligible), (1, sorted(cross.ignored)), (2, sorted(cross.illegal))):
                for names in tuples:
                    spec = x.XCoverageBin()
                    spec.point_id, spec.kind = item_id, kind
                    spec.dimensions = x.XUInt32Vector([bin_ids[(name, value)]
                                            for name, value in zip(cross.definition.points, names)])
                    bins.push_back(spec)
                    self.bin_names.append((cross.definition.name, _tuple_key(names)))
                    self.cross_values[(cross.definition.name, _tuple_key(names))] = tuple(names)
        abort = self.config["abort"]
        abort_root = -1 if abort is None else lower(
            (abort if isinstance(abort, XExpr) else BoundSignalExpr(abort)) != 0)
        group_gate = gate(self.group.definition.iff)
        self.handle = b.arm(self.trigger)
        try:
            engine.AttachCoverage(self._native_handle(), items, bins, group_gate, abort_root,
                                  self.group.illegal_policy.value == "raise", 1024,
                                  self.config["overlap"], self.config["max_active"], self.summary is not None)
        except BaseException:
            b.disarm(self.handle)
            self.handle = None
            raise
        self.native = True
        self.last = [0] * (2 + len(items) * 5 + len(bins))

    def _native_handle(self):
        return self.backend._native_handles[(self.handle.slot, self.handle.generation)]

    def observe(self, phase, tick):
        if self.native or phase is not self.phase or self.last_sample == (phase, tick):
            return
        self.last_sample = phase, tick
        abort = self.config["abort"]
        if abort is not None:
            value = abort.evaluate(None) if isinstance(abort, XExpr) else _read(abort)
            if not isinstance(value, LogicValue) and bool(value):
                self._clear_python_history("aborted")
                return
        self.matcher.phase, self.matcher.tick = phase, tick
        completions = (self._advance_pattern() if self.pattern else
                       int(self.matcher._match(self.watcher) is not None))
        if not completions:
            return
        sample = {}
        for name, signal in self.config["fields"].items():
            target = sample
            parts = name.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = _read(signal)
        for _ in range(completions):
            hits = {} if self.bin_patterns else None
            group_enabled = self.group.definition.iff is None or self.group.definition.iff.enabled(sample)
            for key, state in self.pattern_executions.items():
                point = state["point"]
                enabled = group_enabled and (point.iff is None or point.iff.enabled(sample))
                if not enabled:
                    self._clear_bin(state, "cleared")
                    continue
                spec, trigger, results = state["spec"], state["trigger"], {}
                if isinstance(spec.program, (SequenceSpec, FsmSpec)):
                    count = self._advance_attempts(trigger, state["attempts"], spec.overlap,
                                                   spec.max_active, key, results=results)
                else:
                    count = int(self.matcher._match(state["watcher"]) is not None)
                for bin_key in state["bins"]:
                    terminals = point.bins[bin_key[1]].matcher.terminals
                    hits[bin_key] = sum(results.get(name, 0) for name in set(terminals)) if terminals else count
                self._mirror_bin_diagnostics(state)
            self.group._sample(sample, metadata={"tick": tick, "phase": phase.name}, details=False,
                               diagnostics=self.summary, pattern_hits=hits)

    def _advance_pattern(self):
        return self._advance_attempts(self.trigger, self.attempts, self.config["overlap"],
                                      self.config["max_active"], (None, None))

    def _advance_attempts(self, trigger, attempts, overlap, max_active, key, results=None):
        """Reuse the ordinary trigger interpreter for independent attempts."""
        previous = bool(attempts)
        stats = self.summary.get(key) if self.summary is not None else None
        completions, remaining = 0, []
        def record(event):
            if results is not None:
                terminal = event[3]
                results[terminal] = results.get(terminal, 0) + 1
        for attempt in attempts:
            event = self.matcher._match(attempt)
            matched = event is not None
            failed = (attempt.sequence.failed if isinstance(trigger.program, SequenceSpec)
                      else not matched and attempt.fsm.current == trigger.program.start)
            if matched:
                completions += 1
                record(event)
                if stats is not None:
                    stats["completed"] += 1
            elif failed:
                if stats is not None:
                    stats["expired" if attempt.sequence.expired else "failed"] += 1
            else:
                remaining.append(attempt)
        attempts[:] = remaining
        if overlap or not previous:
            candidate = _Watcher(trigger, 0)
            event = self.matcher._match(candidate)
            matched = event is not None
            progress = (bool(candidate.sequence.index or candidate.sequence.age or candidate.sequence.held)
                        if isinstance(trigger.program, SequenceSpec) else
                        candidate.fsm.current != trigger.program.start)
            if matched or progress:
                if len(attempts) >= max_active:
                    self.group._collection_complete = False
                    raise RuntimeError("coverage active pattern capacity exhausted")
                if stats is not None:
                    stats["started"] += 1
                    stats["peak_active"] = max(stats["peak_active"], len(attempts) + 1)
                    stats["completed"] += int(matched)
                if matched:
                    completions += 1
                    record(event)
                else:
                    attempts.append(candidate)
        return completions

    def _mirror_bin_diagnostics(self, state):
        if self.summary is not None:
            stats = self.summary[state["key"]]
            for key in state["bins"]:
                if key != state["key"]:
                    self.summary[key] = dict(stats)

    def _clear_bin(self, state, reason):
        if self.summary is not None:
            self.summary[state["key"]][reason] += len(state["attempts"])
        state["attempts"].clear()
        state["watcher"] = _Watcher(state["trigger"], 0)
        self._mirror_bin_diagnostics(state)

    def _clear_python_history(self, reason):
        if self.summary is not None:
            if self.pattern:
                self.summary[(None, None)][reason] += len(self.attempts)
            for key, states in self.group._history.items():
                self.summary[key][reason] += len(states)
        self.attempts.clear()
        self.watcher = _Watcher(self.trigger, 0)
        self.group._history.clear()
        for state in self.pattern_executions.values():
            self._clear_bin(state, reason)

    def publish_diagnostics(self):
        patterns = []
        if self.summary is not None:
            for (point, name), stats in self.summary.items():
                identity = ({"kind": "trigger"} if point is None else
                            {"kind": "pattern" if (point, name) in self.bin_patterns else "transition", "point": point, "bin": name})
                patterns.append({**identity, **stats})
        current = dict(collected_runs=int(self.summary is not None),
                       uncollected_runs=int(self.summary is None), patterns=patterns)
        self.group._diagnostics = _merge_diagnostics(self.base_diagnostics, current)

    def sync(self):
        self.check_thread()
        if self.failure is not None:
            raise self.failure
        try:
            self._sync()
        except BaseException as error:
            self.failure = error
            raise

    def _sync(self):
        if not self.native or self.handle is None:
            self.publish_diagnostics()
            return
        snap = self.backend._engine.CoverageSnapshot(self._native_handle())
        if snap.generation != self.handle.generation or snap.epoch != self.epoch:
            raise RuntimeError("coverage snapshot generation/epoch changed outside its owner")
        current = [int(n) for n in snap.counters]
        if len(current) != len(self.last) or any(a < b for a, b in zip(current, self.last)):
            raise RuntimeError("coverage cumulative snapshot is invalid")
        delta = [a - b for a, b in zip(current, self.last)]
        if not (len(snap.illegal_bins) == len(snap.illegal_values) == len(snap.illegal_ticks)):
            raise RuntimeError("coverage diagnostic snapshot is invalid")
        if self.summary is not None:
            expected = (len(self.bin_names) + 1) * len(_DIAGNOSTIC_FIELDS)
            if len(snap.diagnostics) != expected:
                raise RuntimeError("coverage diagnostic counters are invalid")
        illegal = []
        for i in range(self.illegal_seen, len(snap.illegal_bins)):
            item, name = self.bin_names[int(snap.illegal_bins[i])]
            value = self.cross_values.get((item, name), int(snap.illegal_values[i], 16))
            label = _tuple_label(value) if (item, name) in self.cross_values else name
            illegal.append(IllegalHit(self.group.instance, item, label, value,
                                       {"tick": int(snap.illegal_ticks[i]), "phase": self.phase.name}))
        g = self.group
        g.samples += delta[0]
        g.gated += delta[1]
        stats = {**g._points, **g._crosses}
        for i, name in enumerate(self.item_names):
            for j, field in enumerate(("samples", "gated", "ignored", "unmatched", "unknown")):
                setattr(stats[name], field, getattr(stats[name], field) + delta[2 + i * 5 + j])
        offset = 2 + len(self.item_names) * 5
        for i, (item, name) in enumerate(self.bin_names):
            increment = delta[offset + i]
            stats[item].counts[name] += increment
            if increment and (item, name) in self.normal_bins:
                # Native counters provide run attribution, not per-hit timing.
                g._record_hit(stats[item], name, increment, None)
        g._illegal_hits.extend(illegal)
        self.illegal_seen = len(snap.illegal_bins)
        self.last = current
        self.group._collection_complete &= not snap.incomplete
        if self.summary is not None:
            for index, key in self.native_pattern_ids.items():
                for j, field in enumerate(_DIAGNOSTIC_FIELDS):
                    self.summary[key][field] = int(snap.diagnostics[index * len(_DIAGNOSTIC_FIELDS) + j])
        self.publish_diagnostics()

    def inspect(self):
        self.check_thread()
        result = dict(tick=self.backend.tick, phase=self.phase.name, trigger=[], bins={})
        if self.native:
            snap = self.backend._engine.CoverageSnapshot(self._native_handle(), True)
            result["tick"] = int(snap.tick)
            values = [int(n) for n in snap.progress]
            for i in range(0, len(values), 5):
                index, step, age, held, state = values[i:i + 5]
                key = self.native_pattern_ids[index]
                program = (self.trigger.program if key == (None, None) else
                           self.bin_patterns[key]["spec"].program if key in self.bin_patterns else None)
                if isinstance(program, FsmSpec):
                    progress = {"state": program.states[state][0]}
                else:
                    progress = dict(step=step, age=age, held=held)
                self._add_progress(result, key, progress)
        else:
            for attempt in self.attempts:
                progress = ({"state": attempt.fsm.current} if isinstance(self.trigger.program, FsmSpec)
                            else dict(step=attempt.sequence.index, age=attempt.sequence.age,
                                      held=attempt.sequence.held))
                self._add_progress(result, (None, None), progress)
            for key, states in self.group._history.items():
                for state in states:
                    self._add_progress(result, key, dict(step=state.index, age=state.age, held=state.held))
            for key, state in self.bin_patterns.items():
                for attempt in state["attempts"]:
                    progress = ({"state": attempt.fsm.current} if isinstance(state["spec"].program, FsmSpec)
                                else dict(step=attempt.sequence.index, age=attempt.sequence.age,
                                          held=attempt.sequence.held))
                    self._add_progress(result, key, progress)
        return result

    @staticmethod
    def _add_progress(result, key, progress):
        point, name = key
        target = result["trigger"] if point is None else result["bins"].setdefault(point, {}).setdefault(name, [])
        target.append(progress)

    def reset(self, *, counters):
        self.check_thread()
        if self.native:
            self.sync()
            self.backend._engine.ResetCoverage(self._native_handle(), counters)
            if counters:
                self.last = [0] * len(self.last)
                self.epoch += 1
                self.illegal_seen = 0
        else:
            self._clear_python_history("cleared")
        if counters:
            self.base_diagnostics = None
            if self.summary is not None:
                self.summary = {key: _empty_pattern_diagnostics() for key in self.pattern_keys}
        self.group._history.clear()

    def close(self):
        try:
            self.sync()
            if self.summary is not None:
                state = self.inspect()
                if self.pattern:
                    self.summary[(None, None)]["unfinished_at_close"] += len(state["trigger"])
                for point, bins in state["bins"].items():
                    for name, attempts in bins.items():
                        self.summary[(point, name)]["unfinished_at_close"] += len(attempts)
                self.publish_diagnostics()
        finally:
            try:
                if self.handle is not None:
                    self.backend.disarm(self.handle)
            finally:
                self.handle = None
                self.attempts.clear()
                self.backend._coverage_collectors.remove(self)
