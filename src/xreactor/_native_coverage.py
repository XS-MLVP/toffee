"""Private Picker/Verilator adapter for fixture-owned, automatically discovered DUTs."""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import weakref
from typing import Any

from ._artifacts import fingerprint, sync_directory

_POINT = re.compile(rb"C '(.*)' ([0-9]+)\n?\Z")


def _point_counts(path: Path) -> dict[bytes, int]:
    counts = {}
    with path.open("rb") as stream:
        if stream.readline() != b"# SystemC::Coverage-3\n":
            raise ValueError(f"invalid native coverage header: {path}")
        for line in stream:
            match = _POINT.fullmatch(line)
            if match is None or int(match[2]) > 2**63 - 1:
                raise ValueError(f"invalid native coverage record: {path}")
            if match[1] in counts:
                raise ValueError(f"duplicate native coverage points: {path}")
            counts[match[1]] = int(match[2])
    if not counts:
        raise ValueError(f"empty native coverage points: {path}")
    return counts


def _point_digest(counts: dict[bytes, int]) -> str:
    return sha256(b"\n".join(sorted(counts))).hexdigest()


def point_identity(path: Path) -> str:
    return _point_digest(_point_counts(path))


@lru_cache(maxsize=128)
def _file_identity(path: str, size: int, mtime: int) -> str:
    return fingerprint(Path(path))["sha256"]


def _digest(path: Path) -> str:
    s = path.stat()
    return _file_identity(str(path), s.st_size, s.st_mtime_ns)


def _build(dut: Any, override: str | None) -> dict[str, Any]:
    module = sys.modules.get(type(dut).__module__)
    source = getattr(module, "__file__", None)
    if source is None:
        raise ValueError("native DUT class must have an identifiable source module")
    source = Path(source).resolve()
    provenance = source.parent / "build-provenance.json"
    recorded = json.loads(provenance.read_text()) if provenance.is_file() else {}
    tool = shutil.which(override or recorded.get("coverage_processor") or "verilator_coverage")
    if tool is None:
        raise FileNotFoundError("native coverage requires verilator_coverage; use --native-coverage-tool")
    tool_path = Path(tool).resolve()
    files = [source, *sorted(source.parent.glob("*.so")), *sorted(source.parent.glob("*.v")),
             *sorted(source.parent.glob("*.sv"))]
    if provenance.is_file():
        files.append(provenance)
    files = sorted({path.resolve() for path in files})
    build = {str(p): _digest(p) for p in files}
    identity = dict(name=type(dut).__name__, files=build,
                    processor=str(tool_path), processor_sha256=_digest(tool_path))
    identity["build_id"] = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return identity


def merge(inputs: list[Path], output: Path, build: dict[str, Any], points: str) -> None:
    if not inputs or _digest(Path(build["processor"])) != build["processor_sha256"]:
        raise ValueError("missing native inputs or changed coverage processor")
    if any(_digest(Path(path)) != digest for path, digest in build["files"].items()):
        raise ValueError("native DUT build inputs changed during execution")
    expected: dict[bytes, int] = {}
    for path in inputs:
        counts = _point_counts(path)
        if _point_digest(counts) != points:
            raise ValueError(f"native coverage point universe changed: {path}")
        for point, count in counts.items():
            expected[point] = expected.get(point, 0) + count
        del counts
    if any(count > 2**63 - 1 for count in expected.values()):
        raise ValueError("native coverage count sum exceeds the supported counter range")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".native-merge-", dir=output.parent)
    os.close(descriptor)
    pending = Path(name)
    try:
        subprocess.run([build["processor"], "--write", str(pending), *map(str, inputs)],
                       check=True, capture_output=True, text=True)
        actual = _point_counts(pending)
        if _point_digest(actual) != points:
            raise ValueError("native merger changed the coverage point universe")
        if actual != expected:
            point = next(point for point in expected if actual[point] != expected[point])
            raise ValueError(f"native merger changed coverage counts: {point!r}: "
                             f"expected {expected[point]}, got {actual[point]}")
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
        pending.replace(output)
        sync_directory(output.parent)
    finally:
        pending.unlink(missing_ok=True)


def export_lcov(path: Path, output: Path, build: dict[str, Any]) -> bool:
    # Toggle counters must not become DA line hits. Keep the complete original
    # DAT; select only line/branch records for the derivative LCOV export.
    descriptor, name = tempfile.mkstemp(prefix=".line-points-", dir=output.parent)
    os.close(descriptor)
    filtered = Path(name)
    records = 0
    try:
        with path.open("rb") as source, filtered.open("wb") as target:
            target.write(source.readline())
            for line in source:
                match = _POINT.fullmatch(line)
                assert match is not None
                fields = dict(part.split(b"\x02", 1) for part in match[1].split(b"\x01") if b"\x02" in part)
                if fields.get(b"page", b"").startswith((b"v_line", b"v_branch")):
                    target.write(line)
                    records += 1
        if not records:
            return False
        subprocess.run([build["processor"], "--write-info", str(output), str(filtered)],
                       check=True, capture_output=True, text=True)
        return True
    finally:
        filtered.unlink(missing_ok=True)


class NativeCollector:
    def __init__(self, directory: Path, *, keep: bool, tool: str | None, batch: int = 16):
        self.directory, self.keep, self.tool, self.batch = directory, keep, tool, batch
        self.active: list[dict[str, Any]] = []
        self.pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.aggregates: dict[str, dict[str, Any]] = {}
        self.seen: set[int] = set()
        self.registered: dict[int, Any] = {}
        self.inputs = 0
        self.inputs_by_build: dict[str, int] = defaultdict(int)

    def discover(self, value: Any, depth: int = 0) -> None:
        if depth == 0:
            self.seen.clear()  # Revisit factories whose contents changed in the test body.
        if depth > 3 or id(value) in self.seen:
            return
        self.seen.add(id(value))
        if all(callable(getattr(type(value), method, None)) for method in
               ("SetCoverage", "GetCovMetrics", "GetXClock", "Finish")):
            previous = self.registered.get(id(value))
            if previous is not None and previous() is value:
                return
            if not value.GetCovMetrics():
                return
            build = _build(value, self.tool)
            directory = self.directory / "raw"
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, filename = tempfile.mkstemp(prefix="dut-", suffix=".dat", dir=directory)
            os.close(descriptor)
            path = Path(filename)
            value.SetCoverage(str(path))
            self.registered[id(value)] = weakref.ref(value)
            self.active.append(dict(path=str(path), build=build))
            return
        # Common fixture resources expose the generated DUT directly or as
        # agent.dut / factory._instances. Do not traverse arbitrary properties.
        if isinstance(value, dict):
            children = list(value.values())
        elif isinstance(value, (list, tuple)):
            children = value
        else:
            children = [v for k, v in getattr(value, "__dict__", {}).items()
                        if k in ("dut", "_dut", "_instances")]
        for child in children:
            self.discover(child, depth + 1)

    def end_case(self) -> None:
        waiting = []
        for row in self.active:
            if not Path(row["path"]).stat().st_size:
                waiting.append(row)  # A module/session fixture still owns its DUT.
                continue
            row["points"] = point_identity(Path(row["path"]))
            row["input_identity"] = fingerprint(Path(row["path"]))
            self.pending[row["build"]["build_id"]].append(row)
            self.inputs += 1
            self.inputs_by_build[row['build']['build_id']] += 1
        self.active = waiting
        self.seen.clear()
        if sum(map(len, self.pending.values())) >= self.batch:
            self.flush()

    def flush(self) -> None:
        for key, rows in self.pending.items():
            if not rows:
                continue
            build, points = rows[0]["build"], rows[0]["points"]
            if any(r["build"] != build or r["points"] != points for r in rows):
                raise ValueError("native DUT build or point universe changed within a worker")
            previous = self.aggregates.get(key)
            if previous and (previous["build"] != build or previous["points"] != points):
                raise ValueError("native DUT identity changed between batches")
            if previous and fingerprint(Path(previous["artifact"]["path"])) != previous["artifact"]:
                raise ValueError("committed worker native aggregate changed")
            if any(fingerprint(Path(row["path"])) != row["input_identity"] for row in rows):
                raise ValueError("native input changed after DUT export")
            target = self.directory / key / "merged.dat"
            paths = [Path(row["path"]) for row in rows]
            identities = [row["input_identity"] for row in rows]
            merge(([target] if previous else []) + paths, target, build, points)
            self.aggregates[key] = dict(build=build, points=points, artifact=fingerprint(target))
            rows.clear()  # Commit before cleanup: a cleanup error must never re-add this batch.
            if not self.keep:
                for path, identity in zip(paths, identities, strict=True):
                    if fingerprint(path) != identity:
                        raise ValueError("native input changed after merge; retained for inspection")
                    path.unlink()

    def finish(self) -> list[dict[str, Any]]:
        self.end_case()
        if self.active:
            raise ValueError("DUT fixtures must call Finish() before worker shutdown; native coverage was not exported")
        self.flush()
        return [dict(row, input_files=self.inputs_by_build[key]) for key, row in self.aggregates.items()]
