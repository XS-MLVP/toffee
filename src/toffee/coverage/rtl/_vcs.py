"""Normalize Picker's VCS coverage query output."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._model import dataset, json_object, metric, read_json_object


_VCS_KINDS = {"line": "statement", "branch": "branch", "toggle": "toggle",
              "condition": "condition", "fsm": "fsm_transition"}


def from_vcs_json(path: str | Path, *, name: str) -> dict[str, Any]:
    """Read a Picker schema-v1 query, not the old toffee-test summary."""
    report = read_json_object(path)
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or report.get("success") is not True
            or report.get("simulator") != "vcs"):
        raise ValueError("expected a successful Picker VCS query (schema version 1)")
    native = json_object(report.get("metrics"), "Picker VCS metrics")
    metrics = {}
    for key, kind in _VCS_KINDS.items():
        if key in native:
            row = json_object(native[key], f"Picker VCS metric {key}")
            metrics[kind] = metric(row.get("covered"), row.get("total"), f"Picker UCAPI query: {key}")
    query = json_object(report.get("query", {}), "Picker VCS query scope")
    return {**dataset(name, "vcs", "picker-vcs-v1", "Selected Picker query; generated wrapper excluded", metrics),
            "query": query}
