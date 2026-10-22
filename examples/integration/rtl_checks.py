"""Project helpers for observed RTL properties, independent of pytest labels.

Properties keep activation coverage and comparison failures separately. The
current framework supplies sampling phases, subscriptions and cover databases.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from toffee import Bin, CoverGroupDef, CoverPointDef, CoverageDatabase, DriveStable, RisingEdge, on


@dataclass(frozen=True)
class Rule:
    description: str
    scenarios: tuple[str, ...]


class ScenarioChecks:
    def __init__(self, rules: Mapping[str, Rule], *, run_metadata: Mapping[str, Any], contract: str):
        self.run_id = uuid4().hex
        self.evidence_kind = "rtl"
        self.scenarios = {name: frozenset(rule.scenarios) for name, rule in rules.items()}
        self.groups = {
            name: CoverGroupDef(name, (CoverPointDef(
                "scenario", {scenario: Bin.values(scenario) for scenario in rule.scenarios},
                description=rule.description,
            ),), description=rule.description).instantiate(
                name, run_id=self.run_id, run_metadata=run_metadata, contract=contract,
            )
            for name, rule in rules.items()
        }
        self.database = CoverageDatabase(self.groups.values())
        self.failures: list[dict[str, Any]] = []
        self.observer_errors: list[dict[str, Any]] = []
        self.comparisons = {name: 0 for name in rules}
        self.implemented: set[str] = set()

    def check(self, name: str, scenario: str, *, actual: Any, expected: Any,
              tick: int, context: Mapping[str, Any] | None = None) -> None:
        if scenario not in self.scenarios[name]:
            raise ValueError(f"unknown RTL property scenario: {name} [{scenario}]")
        # Activation survives a failing comparison, and is never supplied by
        # the case's name, stimulus settings or pytest outcome.
        metadata = {"tick": tick, "actual": actual, "expected": expected,
                    "context": dict(context or {})}
        self.groups[name].sample({"scenario": scenario}, metadata=metadata, details=False)
        self.comparisons[name] += 1
        if actual != expected:
            failure = {"requirement": name, "scenario": scenario, **metadata}
            self.failures.append(failure)
            raise AssertionError(f"{name} [{scenario}] at tick {tick}: {actual!r} != {expected!r}")

    def status(self) -> dict[str, Any]:
        return {
            "format": "toffee-rtl-property-checks", "run_id": self.run_id,
            "evidence_kind": self.evidence_kind,
            "failures": self.failures,
            "observer_errors": self.observer_errors,
            "requirements": {
                name: {"measured": name in self.implemented,
                       "comparisons": self.comparisons[name],
                       "observed": self.comparisons[name] > 0,
                       "comparison_failures": sum(f["requirement"] == name for f in self.failures),
                       "closed": (name in self.implemented and self.comparisons[name] > 0
                                  and group.covered
                                  and not any(f["requirement"] == name for f in self.failures)
                                  and not self.observer_errors),
                       "uncovered": list(group.uncovered()) if name in self.implemented else None,
                       "coverage": group.coverage if name in self.implemented else None}
                for name, group in self.groups.items()
            },
        }

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        # Unimplemented requirements have no numerical coverage claim.
        measured = CoverageDatabase(self.groups[name] for name in sorted(self.implemented))
        coverage_name = ("internal-functional-coverage.json" if self.evidence_kind == "rtl"
                         else "checker-counterexample-coverage.json")
        measured.write_json(directory / coverage_name)
        (directory / "internal-checks.json").write_text(json.dumps(self.status(), indent=2) + "\n")


class ClockObserver:
    """Capture settled drives and compare after the immediately following edge."""
    def __init__(self, dut, *, prefix: str, signals: Mapping[str, tuple[str, int]]):
        self.dut = dut
        self.signals = {}
        available = set(dut.GetInternalSignalList())
        vpi_available = None
        for name, (path, width) in signals.items():
            full = prefix + path
            use_vpi = full not in available
            if use_vpi:
                if vpi_available is None:
                    vpi_available = set(dut.GetInternalSignalList(use_vpi=True))
                if full not in vpi_available:
                    raise RuntimeError(f"required RTL observation is unavailable: {full}")
            signal = dut.GetInternalSignal(full, use_vpi=use_vpi)
            if signal is None or (int(signal.W()) or 1) != width:
                raise RuntimeError(f"RTL observation has wrong width: {full}, expected {width}")
            self.signals[name] = signal
        self.before = None
        self.before_tick = None
        self.execution = None
        self.subscriptions = []

    def snapshot(self) -> dict[str, int]:
        return {name: int(signal.value) for name, signal in self.signals.items()}

    def start(self, execution):
        if self.execution is not None:
            raise RuntimeError("RTL observer is already started")
        self.execution = execution

        async def consume(_):
            pass

        def capture_before(event):
            self.before = self.snapshot()
            self.before_tick = event.tick

        def capture_after(event):
            if self.before is not None:
                if event.tick != self.before_tick + 1:
                    raise AssertionError("RTL observer missed an acceptance edge")
                after = self.snapshot()
                failure_count = len(self.checks.failures)
                try:
                    self.observe(self.before, after, event.tick)
                except BaseException as error:
                    if len(self.checks.failures) == failure_count:
                        self.checks.observer_errors.append({"tick": event.tick,
                            "error": f"{type(error).__name__}: {error}",
                            "before": self.before, "after": after})
                    raise
                self.before = None

        clock = execution.backend.clock
        try:
            for trigger, capture in ((DriveStable(clock), capture_before),
                                     (RisingEdge(clock), capture_after)):
                self.subscriptions.append(execution.subscribe(on(
                    trigger, capture=capture,
                )(consume).bind()))
        except BaseException:
            for subscription in self.subscriptions:
                execution.reactor.cancel_subscription(subscription)
            self.execution = None
            raise
        return self

    def observe(self, before, after, tick):
        raise NotImplementedError

    async def aclose(self):
        if self.execution is None:
            return
        subscriptions, self.subscriptions = self.subscriptions, []
        for subscription in subscriptions:
            self.execution.reactor.cancel_subscription(subscription)
        await asyncio.gather(*(subscription.task for subscription in subscriptions), return_exceptions=True)
        self.execution = None
        self.before = None
