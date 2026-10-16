"""CLI-loaded pytest/xdist adaptation; no project-owned merge hooks required."""
from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import traceback
from typing import Any

import pytest

from ._artifacts import fingerprint, read_checked, write
from ._coverage_capture import capture, live_groups, suspended
from ._native_coverage import NativeCollector, export_lcov, merge
from .coverage import CoverageDatabase
from .coverage_report import build_unified_coverage_model, parse_lcov, render_unified_coverage_html


class CoverageHooks:
    @pytest.hookspec
    def pytest_xreactor_coverage(self, config, functional, native, pytest_exitstatus):
        """Inspect merged coverage before commit, without exporting a database.

        ``functional`` is the merged report or None; ``native`` contains final
        native artifact identities. Treat both as read-only. An exception
        rejects the coverage transaction and preserves available worker inputs.
        Test failure and coverage completion remain independent.
        """


def pytest_addhooks(pluginmanager):
    pluginmanager.add_hookspecs(CoverageHooks)


def pytest_addoption(parser):
    group = parser.getgroup("XReactor managed execution")
    group.addoption("--xreactor-output")
    group.addoption("--xreactor-keep-coverage-inputs", action="store_true")
    group.addoption("--xreactor-export-coverage-json", action="store_true")
    group.addoption("--xreactor-native-coverage-tool")


def pytest_configure(config):
    config.addinivalue_line("markers", "xreactor_functional_coverage(enabled): exclude synthetic/unit-test groups from automatic measurement; native DUT collection is unaffected")
    if config.getoption("xreactor_output") and not config.option.collectonly:
        plugin = _ExecutionCoverage(config)
        config._xreactor_managed_coverage = plugin
        config.pluginmanager.register(plugin, "xreactor-managed-coverage")


def pytest_unconfigure(config):
    plugin = getattr(config, "_xreactor_managed_coverage", None)
    if plugin is not None:
        plugin.close()


class _WorkerCoverage:
    def __init__(self, root: Path, *, keep: bool, tool: str | None):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=False)
        self.database = CoverageDatabase()
        self.case_groups: dict[int, Any] = {}
        self.persistent_groups: dict[int, Any] = {}
        self.scope = "session"
        self.keep = keep
        self.case_index = 0
        self.native = NativeCollector(root / "native", keep=keep, tool=tool)
        self.errors: list[str] = []
        for group in live_groups():
            self.register(group)

    def register(self, group):
        if self.scope == "case":
            if id(group) not in self.persistent_groups:
                self.case_groups[id(group)] = group
        else:
            self.persistent_groups[id(group)] = group

    def _collect(self, groups):
        # Cloning freezes counters and provenance before the case releases its
        # runtime objects. Reuse the existing schema/origin validation unchanged.
        with suspended():
            incoming = CoverageDatabase()
            for group in groups:
                incoming.merge(CoverageDatabase.from_dict(CoverageDatabase([group]).report()))
            self.database.merge(incoming)
        return incoming

    def end_case(self):
        groups = list(self.case_groups.values())
        incoming = self._collect(groups)
        if self.keep and groups:
            write(self.root / "cases" / f"{self.case_index:08d}.json", incoming.report())
        self.case_groups.clear()
        self.case_index += 1
        self.native.end_case()

    def finish(self, status):
        self._collect(self.persistent_groups.values())
        self.persistent_groups.clear()
        functional = None
        if self.database.groups:
            functional = write(self.root / "functional.json.gz", self.database.report(), compressed=True)
        native = self.native.finish()
        return dict(version=1, worker=self.root.name, pytest_exitstatus=int(status),
                    functional=functional, native=native, native_inputs=self.native.inputs,
                    errors=self.errors)


class _ExecutionCoverage:
    def __init__(self, config):
        self.config = config
        self.root = Path(config.getoption("xreactor_output")).resolve()
        self.worker = getattr(config, "workerinput", {}).get("workerid")
        self.distributed = bool(self.worker or getattr(config.option, "numprocesses", 0))
        self.keep = config.getoption("xreactor_keep_coverage_inputs")
        self.export_json = config.getoption("xreactor_export_coverage_json")
        self.receipts: dict[str, Any] = {}
        self.started: set[str] = set()
        self.errors: list[str] = []
        self.collection: list[str] | None = None
        self.reports: dict[str, list[str]] = {}
        self.coverage = None
        self.capture = None
        if self.worker is None:
            self.root.mkdir(parents=True, exist_ok=False)
            write(self.root / "manifest.json", dict(format="xreactor-execution", version=1, state="running"))
        if self.worker or not self.distributed:
            self.coverage = _WorkerCoverage(self.root / "_workers" / (self.worker or "main"),
                keep=self.keep, tool=config.getoption("xreactor_native_coverage_tool"))
            self.capture = capture(self.coverage)
            self.capture.__enter__()

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node):
        worker = node.gateway.id
        if worker in self.started:
            self.errors.append(f"worker restarted: {worker}")
        self.started.add(worker)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        if self.collection is None:
            self.collection = list(ids)
        elif self.collection != list(ids):
            self.errors.append(f"worker collection differs: {node.gateway.id}")

    def pytest_collection_finish(self, session):
        if not self.distributed:
            self.collection = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append(f"collection failed: {report.nodeid}")

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef, request):
        if self.coverage is None:
            yield
            return
        old_scope = self.coverage.scope
        self.coverage.scope = "case" if fixturedef.scope == "function" else "session"
        try:
            outcome = yield
            if outcome.excinfo is None:
                try:
                    self.coverage.native.discover(outcome.get_result())
                except Exception:
                    self.coverage.errors.append(traceback.format_exc())
                    raise
        finally:
            self.coverage.scope = old_scope

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item):
        marker = item.get_closest_marker("xreactor_functional_coverage")
        if marker is not None and (len(marker.args) != 1 or type(marker.args[0]) is not bool or marker.kwargs):
            raise pytest.UsageError("xreactor_functional_coverage requires one boolean argument")
        enabled = marker is None or marker.args[0]
        if self.coverage:
            self.coverage.scope = "case"
        try:
            with nullcontext() if enabled else suspended():
                yield
        finally:
            if self.coverage:
                self.coverage.scope = "session"

    def pytest_runtest_call(self, item):
        if self.coverage:
            try:
                for value in item.funcargs.values():
                    self.coverage.native.discover(value)
            except Exception:
                self.coverage.errors.append(traceback.format_exc())
                raise

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        discovery_error = None
        if self.coverage:
            try:
                # Factories may have constructed DUTs in the test body. Route
                # their exports before the owning fixtures call Finish().
                for value in item.funcargs.values():
                    self.coverage.native.discover(value)
            except Exception as error:
                discovery_error = error
                self.coverage.errors.append(traceback.format_exc())
        outcome = yield
        if self.coverage:
            try:
                self.coverage.end_case()
            except Exception:
                self.coverage.errors.append(traceback.format_exc())
                if outcome.excinfo is None:
                    raise
        if discovery_error is not None and outcome.excinfo is None:
            raise discovery_error

    def pytest_runtest_logreport(self, report):
        if self.worker:
            return
        state = ("XFAIL" if report.skipped else "XPASS") if hasattr(report, "wasxfail") else (
            "ERROR" if report.failed and report.when != "call" else
            "XPASS" if report.failed and str(report.longrepr).startswith("[XPASS(strict)]") else
            "FAIL" if report.failed else "SKIP" if report.skipped else
            "PASS" if report.when == "call" else None)
        if state is not None:
            self.reports.setdefault(report.nodeid, []).append(state)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        worker = node.gateway.id
        output = getattr(node, "workeroutput", {})
        row = output.get("xreactor_coverage")
        if error or row is None or worker in self.receipts:
            detail = error or output.get("xreactor_coverage_error", "missing or duplicate receipt")
            self.errors.append(f"worker did not finish cleanly: {worker}: {detail}")
        else:
            self.receipts[worker] = row

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        try:
            if self.coverage:
                receipt = self.coverage.finish(exitstatus)
                identity = write(self.coverage.root / "receipt.json", receipt)
                if self.worker:
                    self.config.workeroutput["xreactor_coverage"] = identity
                    return
                self.receipts["main"] = identity
            self._finalize(session)
        except Exception:
            if not session.exitstatus:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
            error = traceback.format_exc()
            if self.worker:
                self.config.workeroutput["xreactor_coverage_error"] = error
            else:
                write(self.root / "manifest.json", dict(format="xreactor-execution", version=1,
                    state="incomplete", pytest_exitstatus=int(session.exitstatus),
                    errors=[*self.errors, error], inputs_retained=True))
            reporter = self.config.pluginmanager.getplugin("terminalreporter")
            if reporter:
                reporter.write_line(error, red=True)

    def _finalize(self, session):
        existing = json.loads((self.root / "manifest.json").read_text())
        if existing.get("state") == "committed":
            # Recovery after an interrupted cleanup consumes no worker input
            # again. Verify the committed outputs, then finish cleanup only.
            for row in existing["artifacts"]:
                expected = {key: row[key] for key in ("path", "size", "sha256")}
                path = Path(row["path"])
                if not path.is_relative_to(self.root) or fingerprint(path) != expected:
                    raise ValueError("committed coverage artifact changed before cleanup recovery")
            self._cleanup(existing, session)
            return
        if self.distributed and self.started != set(self.receipts):
            self.errors.append("not every started worker produced a receipt")
        if self.errors or session.exitstatus not in (0, 1):
            raise ValueError(f"incomplete execution: {self.errors}, exit={session.exitstatus}")
        database = CoverageDatabase()
        native: dict[str, list[dict[str, Any]]] = {}
        worker_metadata = []
        native_paths: set[str] = set()
        for worker, identity in sorted(self.receipts.items()):
            root = self.root / "_workers" / worker
            receipt = read_checked(identity, root)
            if receipt["errors"]:
                raise ValueError(f"worker coverage failed: {worker}:\n" + "\n".join(receipt["errors"]))
            if receipt["worker"] != worker or receipt["pytest_exitstatus"] not in (0, 1):
                raise ValueError(f"invalid worker receipt: {worker}")
            if receipt["functional"]:
                with suspended():
                    database.merge(CoverageDatabase.from_dict(read_checked(receipt["functional"], root, compressed=True)))
            worker_builds: set[str] = set()
            for row in receipt["native"]:
                artifact = row["artifact"]
                key = row["build"]["build_id"]
                if key in worker_builds or artifact["path"] in native_paths:
                    raise ValueError("duplicate worker native contribution")
                worker_builds.add(key)
                native_paths.add(artifact["path"])
                if not Path(artifact["path"]).is_relative_to(root) or fingerprint(Path(artifact["path"])) != artifact:
                    raise ValueError("native input identity mismatch")
                native.setdefault(row["build"]["build_id"], []).append(row)
            worker_metadata.append(dict(worker=worker, receipt_sha256=identity["sha256"],
                native_inputs=receipt["native_inputs"]))
        artifacts, lines = [], []
        for key, rows in sorted(native.items()):
            build, points = rows[0]["build"], rows[0]["points"]
            if any(row["build"] != build or row["points"] != points for row in rows):
                raise ValueError("worker native build or point universe differs")
            target = self.root / "native" / key / "coverage.dat"
            merge([Path(row["artifact"]["path"]) for row in rows], target, build, points)
            info = target.with_suffix(".info")
            artifacts.append(dict(kind="native", build=build,
                input_files=sum(row["input_files"] for row in rows), **fingerprint(target)))
            if export_lcov(target, info, build):
                lines.append(parse_lcov(info, name=build["name"]))
                artifacts.append(dict(kind="lcov", **fingerprint(info)))
        functional = database.report() if database.groups else None
        if self.export_json and functional:
            artifacts.append(dict(kind="functional", **write(self.root / "functional-coverage.json", functional)))
        if functional or lines or native:
            report = self.root / "coverage.html"
            render_unified_coverage_html(build_unified_coverage_model(functional=functional, line_coverage=lines,
                title=f"XReactor coverage (test exit status {int(session.exitstatus)})"), report)
            artifacts.append(dict(kind="html", **fingerprint(report)))
        counts = Counter(next((s for s in ("ERROR", "FAIL", "XPASS", "XFAIL", "SKIP", "PASS") if s in states), "ERROR")
                         for states in self.reports.values())
        if self.collection is None or set(self.reports) != set(self.collection):
            raise ValueError("reported tests differ from collected tests")
        self.config.hook.pytest_xreactor_coverage(config=self.config, functional=functional,
            native=[row for row in artifacts if row["kind"] == "native"],
            pytest_exitstatus=int(session.exitstatus))
        manifest = dict(format="xreactor-execution", version=1, state="committed",
            finished_at=datetime.now(timezone.utc).isoformat(), pytest_exitstatus=int(session.exitstatus),
            tests=dict(counts), workers=worker_metadata, coverage_complete=True,
            functional_groups=len(database.groups), artifacts=artifacts,
            keep_coverage_inputs=self.keep, cleanup_complete=False)
        write(self.root / "manifest.json", manifest)
        # Commit precedes cleanup. The final manifest never requires consumed
        # input paths to exist; input receipt hashes remain provenance.
        self._cleanup(manifest, session)

    def _cleanup(self, manifest, session):
        try:
            if not self.keep:
                inputs = self.root / "_workers"
                if inputs.exists():
                    shutil.rmtree(inputs)
            manifest["cleanup_complete"] = True
            manifest.pop("cleanup_error", None)
        except OSError as error:
            # The data transaction is already committed. Preserve that fact;
            # a deletion failure must not turn into a second additive merge.
            manifest["cleanup_error"] = str(error)
            if not session.exitstatus:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
        write(self.root / "manifest.json", manifest)

    def pytest_terminal_summary(self, terminalreporter):
        if not self.worker:
            terminalreporter.write_line(f"XReactor artifacts: {self.root}")

    def close(self):
        if self.capture is not None:
            self.capture.__exit__(None, None, None)
            self.capture = None
