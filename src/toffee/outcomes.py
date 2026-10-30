"""Test result vocabulary shared by the runner and project report adapters.

UNCERTAIN is an evidence declaration, not a pytest failure or an interrupted
execution.  The checks still run; only an otherwise successful case becomes
UNCERTAIN.  Pytest's exit-code policy is unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


UNCERTAIN_PROPERTY = "toffee_uncertain"


def uncertain_declaration(report: Any) -> dict[str, str] | None:
    """Read the declaration carried by a pytest report, including via xdist."""
    for name, value in getattr(report, "user_properties", ()):
        if name == UNCERTAIN_PROPERTY and isinstance(value, dict):
            return dict(value)
    return None


def phase_record(report: Any) -> dict[str, Any]:
    """Serialize a pytest phase without losing strict XPASS or UNCERTAIN."""
    row = dict(outcome=report.outcome, duration=report.duration,
               wasxfail=getattr(report, "wasxfail", None))
    if report.failed and str(report.longrepr).startswith("[XPASS(strict)]"):
        row["strict_xpass"] = True
    declaration = uncertain_declaration(report)
    if declaration is not None:
        row["uncertain"] = declaration
    return row


def report_status(report: Any) -> str | None:
    """Classify one pytest phase; setup/teardown success is not a case PASS."""
    if report.failed:
        if report.when != "call":
            return "ERROR"
        return "XPASS" if str(report.longrepr).startswith("[XPASS(strict)]") else "FAIL"
    if hasattr(report, "wasxfail"):
        return "XFAIL" if report.skipped else "XPASS"
    if report.skipped:
        return "SKIP"
    if report.when == "call" and report.passed:
        return "UNCERTAIN" if uncertain_declaration(report) else "PASS"
    return None


def case_status(phases: Mapping[str, Mapping[str, Any]]) -> str:
    """Classify a complete case, giving failures precedence over declarations.

    A call can pass before its teardown fails.  Aggregate all phases rather
    than trusting the call's provisional status.  Missing phases remain
    INCOMPLETE and must never be relabelled UNCERTAIN.
    """
    if any(row.get("outcome") == "failed" for phase, row in phases.items() if phase != "call"):
        return "ERROR"
    call = phases.get("call", {})
    if call.get("outcome") == "failed":
        return "XPASS" if call.get("strict_xpass") else "FAIL"
    if any(row.get("wasxfail") is not None for row in phases.values()):
        return "XPASS" if call.get("outcome") == "passed" else "XFAIL"
    if any(row.get("outcome") == "skipped" for row in phases.values()):
        return "SKIP"
    if set(phases) != {"setup", "call", "teardown"} or call.get("outcome") != "passed":
        return "INCOMPLETE"
    return "UNCERTAIN" if call.get("uncertain") else "PASS"
