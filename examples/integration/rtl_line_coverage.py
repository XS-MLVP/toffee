"""Compare Verilator execution counters for one RTL build, keeping raw scope.

Picker's --coverage also enables toggles. Combining different counter types
in LCOV can obscure whether a procedural block executed. Report
v_line/v_branch counters separately, including every zero counter. Module
exports have distinct hierarchies and must be reported separately.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess


RECORD = re.compile(r"C '(.*)' ([0-9]+)")
EXECUTION_KINDS = {"v_line", "v_branch"}


def fields(key):
    return dict(part.split("\x02", 1) for part in key.split("\x01") if "\x02" in part)


def read_runs(paths, top):
    files = sorted({file.resolve() for path in paths
                    for file in (path.rglob("*.dat") if path.is_dir() else [path])})
    if not files:
        raise ValueError("no Verilator coverage databases found")
    counts, witnesses = defaultdict(int), defaultdict(list)
    for file in files:
        if not file.read_text().startswith("# SystemC::Coverage-"):
            raise ValueError(f"not a Verilator coverage database: {file}")
        for line in file.read_text().splitlines():
            match = RECORD.fullmatch(line)
            if match is None:
                continue
            key, value = match.group(1), int(match.group(2))
            hierarchy = fields(key).get("h", "")
            if hierarchy != top and not hierarchy.startswith(top + "."):
                continue
            counts[key] += value
            if value:
                witnesses[key].append(str(file))
    if not counts:
        raise ValueError(f"no coverage points in hierarchy {top}")
    return dict(counts), dict(witnesses), [str(file) for file in files]


def metric(values):
    values = list(values)
    covered = sum(value > 0 for value in values)
    return {"covered": covered, "total": len(values),
            "percent": round(100 * covered / len(values), 4) if values else None}


def source_lines(counts):
    lines = defaultdict(int)
    for key, count in counts.items():
        point = fields(key)
        # S names all source lines represented by this block counter. Keep
        # ranges and disjoint spans exactly as Verilator's LCOV conversion.
        numbers = {int(point["l"])}
        for span in point.get("S", "").split(","):
            if not span:
                continue
            ends = [int(value) for value in span.split("-")]
            numbers.update(range(ends[0], ends[-1] + 1))
        for number in numbers:
            lines[point["f"], number] += count
    return lines


def describe(key):
    point = fields(key)
    path, number = Path(point["f"]), int(point["l"])
    source = path.read_text().splitlines() if path.is_file() else []
    return {"file": str(path), "line": number, "kind": point["page"].split("/")[0],
            "page": point["page"], "arm": point.get("o"), "hierarchy": point["h"],
            "column": point.get("n"), "source_lines": point.get("S", str(number)),
            "source": source[number - 1].strip() if number <= len(source) else None}


def lcov(counts, path):
    database = path.with_suffix(".dat")
    database.write_text("# SystemC::Coverage-3\n" + "".join(
        f"C '{key}' {count}\n" for key, count in sorted(counts.items())))
    subprocess.run(["verilator_coverage", "--write-info", str(path), str(database)],
                   check=True, capture_output=True, text=True)
    # Keep the installed converter's behavior, including its DA treatment of
    # multiple points. Individual counters remain available in the .dat file.
    return metric(int(line[3:].split(",")[1]) for line in path.read_text().splitlines()
                  if line.startswith("DA:"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, nargs="+", required=True)
    parser.add_argument("--added", type=Path, nargs="+", required=True)
    parser.add_argument("--rtl", type=Path, nargs="+", required=True)
    parser.add_argument("--top", required=True, help="exact build hierarchy, e.g. TOP.CacheSignalCFG_top")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before, _, baseline_files = read_runs(args.before, args.top)
    added, witnesses, added_files = read_runs(args.added, args.top)
    if set(before) != set(added):
        raise ValueError("coverage schemas differ; compare runs of the same DUT build")
    after = {key: count + added[key] for key, count in before.items()}
    rtl = {str(path.resolve()) for path in args.rtl}
    if not rtl <= {fields(key)["f"] for key in before}:
        raise ValueError("requested RTL source is absent from the coverage database")

    def select(counts, execution=False, design=False):
        return {key: count for key, count in counts.items()
                if (not design or fields(key)["f"] in rtl)
                and (not execution or fields(key)["page"].split("/")[0] in EXECUTION_KINDS)}

    args.output.mkdir(parents=True, exist_ok=True)

    def summary(counts, label):
        design = select(counts, design=True)
        execution = select(counts, execution=True, design=True)
        return {
            "all_instrumentation_source_lines": lcov(counts, args.output / f"all-{label}.info"),
            "rtl_all_instrumentation_source_lines": lcov(design, args.output / f"rtl-all-{label}.info"),
            "rtl_execution_source_lines": lcov(execution, args.output / f"execution-{label}.info"),
            "rtl_any_execution_point_source_lines": metric(source_lines(execution).values()),
            "rtl_execution_points": metric(execution.values()),
            "rtl_line_block_points": metric(count for key, count in execution.items()
                                           if fields(key)["page"].startswith("v_line/")),
            "rtl_branch_points": metric(count for key, count in execution.items()
                                       if fields(key)["page"].startswith("v_branch/")),
        }

    execution = select(after, execution=True, design=True)
    new = [key for key, count in execution.items() if count and not before[key]]
    remaining = [key for key, count in execution.items() if not count]
    report = {
        "format": "toffee-verilator-execution-comparison/v1", "top": args.top,
        "scope": {"rtl": [{"path": path, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                           for path in sorted(rtl)],
                  "baseline_databases": baseline_files, "added_databases": added_files,
                  "excluded_execution_points": []},
        "definitions": {
            "source_line_hit": "DA count from the installed verilator_coverage --write-info is positive",
            "any_point_source_line_hit": "at least one execution counter spanning this line is positive; not all arms necessarily executed",
            "execution_point_hit": "individual v_line or v_branch counter is positive; toggle counters do not contribute",
            "after": "baseline plus added runs of the identical coverage schema",
        },
        "verilator_coverage_version": subprocess.run(["verilator_coverage", "--version"],
            check=True, capture_output=True, text=True).stdout.strip(),
        "before": summary(before, "before"), "after": summary(after, "after"),
        "newly_hit_execution_points": [{**describe(key), "count": added[key], "cases": witnesses[key]} for key in new],
        "remaining_execution_points": [describe(key) for key in remaining],
    }
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"before": report["before"], "after": report["after"],
                      "new_points": len(new), "remaining_points": len(remaining)}, indent=2))


if __name__ == "__main__":
    main()
