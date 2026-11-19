"""Shared metric names, counting states, and dataset validation."""
from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
from typing import Any


METRICS = {
    "line": "source line",
    "statement": "statement",
    "block": "basic block",
    "branch": "branch point",
    "condition": "condition bin",
    "expression": "expression bin",
    "toggle": "toggle point",
    "fsm_state": "state",
    "fsm_transition": "transition",
    "fsm_native": "native FSM target",
}


def metric(covered: Any, total: Any, basis: str, *, unit: str | None = None) -> dict[str, Any]:
    if type(covered) is not int or type(total) is not int or not 0 <= covered <= total:
        raise ValueError(f"invalid coverage counts for {basis}: {covered!r}/{total!r}")
    return dict(status="measured" if total else "no_targets", basis=basis,
                covered=covered, total=total, unit=unit)


def unavailable(unit: str) -> dict[str, Any]:
    return dict(status="unavailable", basis=None, covered=None, total=None, unit=unit)


def dataset(name: str, simulator: str, source_format: str, scope: str,
            metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = {}
    for kind, unit in METRICS.items():
        if kind in metrics:
            row = metrics[kind]
            rows[kind] = {**row, "unit": row["unit"] or unit}
        else:
            rows[kind] = unavailable(unit)
    return dict(name=name, simulator=simulator, source_format=source_format, scope=scope, metrics=rows)


def json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def read_json_object(path: str | Path) -> dict[str, Any]:
    return json_object(json.loads(Path(path).read_text(encoding="utf-8")), str(path))


def validate_datasets(datasets: Any, kinds: Iterable[str]) -> list[dict[str, Any]]:
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("RTL coverage report requires at least one dataset")
    expected = set(kinds)
    for row in datasets:
        row = json_object(row, "RTL coverage dataset")
        for key in ("name", "simulator", "source_format", "scope"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"missing RTL coverage dataset field: {key}")
        metrics = json_object(row.get("metrics"), "RTL coverage metrics")
        if metrics.keys() != expected:
            raise ValueError("invalid RTL coverage metric set")
        for kind, value in metrics.items():
            value = json_object(value, f"RTL coverage metric {kind}")
            status, covered, total = value.get("status"), value.get("covered"), value.get("total")
            if not isinstance(value.get("unit"), str) or not value["unit"].strip():
                raise ValueError(f"missing RTL coverage unit for {kind}")
            if status == "unavailable":
                if not {"covered", "total", "basis"} <= value.keys() or any(
                    item is not None for item in (covered, total, value["basis"])
                ):
                    raise ValueError(f"unavailable RTL coverage metric has data: {kind}")
            elif (not isinstance(value.get("basis"), str) or not value["basis"].strip()
                  or status != metric(covered, total, kind)["status"]):
                raise ValueError(f"invalid RTL coverage status or basis: {kind}")
    return datasets
