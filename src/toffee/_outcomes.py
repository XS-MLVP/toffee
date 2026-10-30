"""Pytest display and declaration support, independent of coverage collection."""
from __future__ import annotations

import pytest

from .outcomes import UNCERTAIN_PROPERTY, case_status, phase_record, report_status


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "uncertain(reason, reference=None): execute all checks, but record an "
        "otherwise successful case as UNCERTAIN because its evidence is insufficient")
    config._toffee_outcome_phases = {}


def pytest_collection_modifyitems(items):
    for item in items:
        marker = item.get_closest_marker("uncertain")
        if marker is None:
            continue
        if marker.args or set(marker.kwargs) - {"reason", "reference"}:
            raise pytest.UsageError(
                f"{item.nodeid}: uncertain accepts reason=... and optional reference=... only")
        reason = marker.kwargs.get("reason")
        reference = marker.kwargs.get("reference")
        if not isinstance(reason, str) or not reason.strip():
            raise pytest.UsageError(f"{item.nodeid}: uncertain requires a non-empty reason")
        if reference is not None and (not isinstance(reference, str) or not reference.strip()):
            raise pytest.UsageError(f"{item.nodeid}: uncertain reference must be a non-empty string")
        if item.get_closest_marker("xfail") is not None:
            raise pytest.UsageError(f"{item.nodeid}: uncertain and xfail cannot be combined")
        declaration = {"reason": reason.strip()}
        if reference is not None:
            declaration["reference"] = reference.strip()
        # Pytest copies user_properties to every report; xdist serializes them.
        item.user_properties.append((UNCERTAIN_PROPERTY, declaration))


@pytest.hookimpl(tryfirst=True)
def pytest_report_teststatus(report, config):
    if report_status(report) == "UNCERTAIN":
        return "uncertain", "U", ("UNCERTAIN", {"yellow": True})
    return None


class OutcomeSession:
    def __init__(self, config):
        self.config = config
        self.phases = config._toffee_outcome_phases

    def pytest_runtest_logreport(self, report):
        self.phases.setdefault(report.nodeid, {})[report.when] = phase_record(report)

    def pytest_sessionfinish(self, session):
        terminal = self.config.pluginmanager.getplugin("terminalreporter")
        if terminal is not None:
            # A provisional U followed by teardown ERROR must not remain in the
            # final uncertain count.  The error report itself is left intact.
            terminal.stats["uncertain"] = [
                report for report in terminal.stats.get("uncertain", [])
                if case_status(self.phases.get(report.nodeid, {})) == "UNCERTAIN"]

    def pytest_terminal_summary(self, terminalreporter):
        rows = [(nodeid, phases["call"]["uncertain"])
                for nodeid, phases in sorted(self.phases.items())
                if case_status(phases) == "UNCERTAIN"]
        if not rows:
            return
        terminalreporter.write_sep("=", "UNCERTAIN: evidence is not yet sufficient", yellow=True)
        for nodeid, row in rows:
            reference = f" [{row['reference']}]" if row.get("reference") else ""
            terminalreporter.write_line(f"{nodeid}: {row['reason']}{reference}")
        terminalreporter.write_line(
            "Checks ran; UNCERTAIN is not PASS. It does not change the pytest exit code.")


def pytest_sessionstart(session):
    session.config.pluginmanager.register(OutcomeSession(session.config), "toffee-outcomes-session")
