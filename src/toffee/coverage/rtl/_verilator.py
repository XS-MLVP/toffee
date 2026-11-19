"""Read Verilator DAT records for offline reports and native aggregation."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
import re
from typing import Any

from ._model import dataset, metric


DAT_RECORD = re.compile(rb"C '(.*)' ([0-9]+)\n?\Z")
_DAT_KINDS = {"line": "block", "branch": "branch", "expr": "expression",
              "toggle": "toggle", "fsm_state": "fsm_state", "fsm_arc": "fsm_transition"}


def point_counts(path: Path) -> dict[bytes, int]:
    counts = {}
    with path.open("rb") as stream:
        if stream.readline() != b"# SystemC::Coverage-3\n":
            raise ValueError(f"invalid native coverage header: {path}")
        for line in stream:
            match = DAT_RECORD.fullmatch(line)
            if match is None or int(match[2]) > 2**63 - 1:
                raise ValueError(f"invalid native coverage record: {path}")
            if match[1] in counts:
                raise ValueError(f"duplicate native coverage points: {path}")
            counts[match[1]] = int(match[2])
    if not counts:
        raise ValueError(f"empty native coverage points: {path}")
    return counts


def point_fields(key: bytes) -> dict[bytes, bytes]:
    return dict(part.split(b"\x02", 1) for part in key.split(b"\x01") if b"\x02" in part)


def _line_spans(fields: dict[bytes, bytes]) -> Iterator[tuple[int, int]]:
    anchor = int(fields[b"l"])
    if anchor <= 0:
        raise ValueError("invalid Verilator source line")
    yield anchor, anchor
    for span in fields.get(b"S", b"").split(b","):
        if not span:
            continue
        match = re.fullmatch(rb"([0-9]+)(?:-([0-9]+))?", span)
        if match is None:
            raise ValueError(f"invalid Verilator line span: {span!r}")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 0 < start <= end:
            raise ValueError(f"invalid Verilator line span: {span!r}")
        yield start, end


def _count_lines(events: Counter[int]) -> int:
    """Length of the union of inclusive spans, without expanding line ranges."""
    count = active = previous = 0
    for line, change in sorted(events.items()):
        if active:
            count += line - previous
        active += change
        previous = line
    return count


def from_verilator_dat(path: str | Path, *, name: str) -> dict[str, Any]:
    """Count raw point types separately; only line points contribute to source lines."""
    totals: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    lines: dict[bytes, Counter[int]] = defaultdict(Counter)
    hit_lines: dict[bytes, Counter[int]] = defaultdict(Counter)
    unmapped = False
    for key, count in point_counts(Path(path)).items():
        fields = point_fields(key)
        raw_kind = fields.get(b"t") or fields.get(b"page", b"").split(b"/", 1)[0].removeprefix(b"v_")
        kind = _DAT_KINDS.get(raw_kind.decode("ascii", errors="replace"))
        if kind is None:
            continue
        hit = count > 0
        totals[kind] += 1
        hits[kind] += hit
        if kind == "block":
            if not fields.get(b"f") or fields.get(b"l") in (None, b"", b"0"):
                unmapped = True
                continue
            for start, end in _line_spans(fields):
                lines[fields[b"f"]][start] += 1
                lines[fields[b"f"]][end + 1] -= 1
                if hit:
                    hit_lines[fields[b"f"]][start] += 1
                    hit_lines[fields[b"f"]][end + 1] -= 1
    metrics = {kind: metric(hits[kind], total, "verilator.dat: point counter > 0")
               for kind, total in totals.items()}
    if lines and not unmapped:
        metrics["line"] = metric(sum(map(_count_lines, hit_lines.values())), sum(map(_count_lines, lines.values())),
                                 "verilator.dat: source lines from line points only; any hit")
    return dataset(name, "verilator", "verilator-dat",
                   "All points in the supplied DAT, including any recorded FSM reset transitions", metrics)
