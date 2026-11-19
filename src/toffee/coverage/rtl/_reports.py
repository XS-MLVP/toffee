"""Read and write Toffee's per-metric RTL coverage reports."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..._artifact_io import write
from ._model import METRICS, read_json_object, unavailable, validate_datasets


FORMAT = "toffee-rtl-coverage"
METRIC_FORMAT = "toffee-rtl-coverage-metric"
VERSION = 1
METRIC_GROUPS = {
    "line": ("line",),
    "statement": ("statement",),
    "block": ("block",),
    "branch": ("branch",),
    "condition": ("condition",),
    "expression": ("expression",),
    "toggle": ("toggle",),
    "fsm": ("fsm_state", "fsm_transition", "fsm_native"),
}


def metric_paths(root: Path) -> dict[str, Path]:
    return {group: root / f"{group}.json" for group in METRIC_GROUPS}


def write_metric_reports(directory: str | Path, datasets: list[dict[str, Any]]) -> None:
    """Write independent per-metric files to a new directory, never replacing reports."""
    validate_datasets(datasets, METRICS)
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=False)
    export_id = uuid4().hex
    for group, path in metric_paths(root).items():
        rows = []
        for index, dataset in enumerate(datasets):
            metadata = {key: value for key, value in dataset.items() if key != "metrics"}
            rows.append({**metadata, "dataset_id": index, "metrics": {
                kind: dataset["metrics"][kind] for kind in METRIC_GROUPS[group]
            }})
        write(path, dict(format=METRIC_FORMAT, version=VERSION, export_id=export_id,
                         metric=group, datasets=rows))


def _read_report(path: Path, *, expected_metric: str | None = None) -> tuple[str | None, list[dict[str, Any]]]:
    report = read_json_object(path)
    if type(report.get("version")) is not int or report["version"] != VERSION:
        raise ValueError(f"unsupported RTL coverage format/version: {path}")
    if report.get("format") == FORMAT and expected_metric is None:
        return None, validate_datasets(report.get("datasets"), METRICS)
    if report.get("format") != METRIC_FORMAT:
        raise ValueError(f"unsupported RTL coverage format/version: {path}")
    group = report.get("metric")
    if not isinstance(group, str) or group not in METRIC_GROUPS or (
        expected_metric is not None and group != expected_metric
    ):
        raise ValueError(f"invalid RTL coverage metric in {path}: {group!r}")
    export_id = report.get("export_id")
    if not isinstance(export_id, str) or not export_id.strip():
        raise ValueError(f"missing RTL coverage export ID: {path}")
    rows = validate_datasets(report.get("datasets"), METRIC_GROUPS[group])
    ids = [row.get("dataset_id") for row in rows]
    if any(type(key) is not int or key < 0 for key in ids) or len(set(ids)) != len(ids):
        raise ValueError(f"invalid or duplicate RTL coverage dataset ID: {path}")
    return export_id, rows


def _read_metric_directory(root: Path) -> Iterable[list[dict[str, Any]]]:
    expected_id = None
    for group, file in metric_paths(root).items():
        if not file.exists():
            continue
        export_id, rows = _read_report(file, expected_metric=group)
        if expected_id is not None and export_id != expected_id:
            raise ValueError(f"RTL coverage files come from different exports: {file}")
        expected_id = export_id
        yield rows


def _combine_metrics(files: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    datasets: dict[int, dict[str, Any]] = {}
    for rows in files:
        for row in rows:
            key = row["dataset_id"]
            metadata = {field: value for field, value in row.items() if field not in {"dataset_id", "metrics"}}
            record = datasets.setdefault(key, {**metadata, "metrics": {}})
            if (metadata != {field: value for field, value in record.items() if field != "metrics"}
                    or record["metrics"].keys() & row["metrics"].keys()):
                raise ValueError(f"inconsistent RTL coverage dataset ID: {key}")
            record["metrics"].update(row["metrics"])
    result = []
    for _, dataset in sorted(datasets.items()):
        rows = dataset["metrics"]
        dataset["metrics"] = {kind: rows.get(kind, unavailable(unit)) for kind, unit in METRICS.items()}
        result.append(dataset)
    return result


def read_report(path: str | Path) -> list[dict[str, Any]]:
    """Read a directory, one metric file, or an earlier single-file summary."""
    root = Path(path)
    if not root.is_dir():
        export_id, rows = _read_report(root)
        return _combine_metrics([rows]) if export_id is not None else rows
    result = _combine_metrics(_read_metric_directory(root))
    if not result:
        raise ValueError(f"no RTL coverage metric files found in {root}")
    return result
