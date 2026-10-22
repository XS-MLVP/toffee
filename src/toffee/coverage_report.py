"""Unified functional and simulator code-coverage HTML reports.

The code-coverage boundary is LCOV rather than a simulator-specific database.
Verilator can emit LCOV with ``verilator_coverage --write-info``; other
simulators can use their corresponding LCOV exporter without changing the
report renderer.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from .coverage import CoverageDatabase


REPORT_SCHEMA_VERSION = 1


def _percentage(hit: int, found: int) -> float:
    return hit * 100.0 / found if found else 0.0


def _line_ranges(lines: Iterable[int]) -> list[str]:
    ordered = sorted(set(lines))
    if not ordered:
        return []
    ranges: list[str] = []
    start = previous = ordered[0]
    for line in ordered[1:]:
        if line == previous + 1:
            previous = line
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = line
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ranges


def parse_lcov(path: str | Path, *, name: str | None = None) -> dict[str, Any]:
    """Parse an LCOV tracefile into the report's simulator-neutral model."""

    source = Path(path)
    files: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            filename = line[3:]
            current = files.setdefault(
                filename,
                {"lines": {}, "reported_found": 0, "reported_hit": 0},
            )
        elif current is not None and line.startswith("DA:"):
            fields = line[3:].split(",")
            if len(fields) < 2:
                raise ValueError(f"malformed LCOV DA record in {source}: {raw!r}")
            line_number, count = int(fields[0]), int(fields[1])
            current["lines"][line_number] = current["lines"].get(line_number, 0) + count
        elif current is not None and line.startswith("LF:"):
            current["reported_found"] += int(line[3:])
        elif current is not None and line.startswith("LH:"):
            current["reported_hit"] += int(line[3:])
        elif line == "end_of_record":
            current = None

    if not files:
        raise ValueError(f"LCOV tracefile contains no source records: {source}")

    normalized: list[dict[str, Any]] = []
    total_found = total_hit = 0
    for filename, record in sorted(files.items()):
        line_counts: dict[int, int] = record["lines"]
        if line_counts:
            found = len(line_counts)
            hit = sum(count > 0 for count in line_counts.values())
            uncovered = _line_ranges(
                line_number
                for line_number, count in line_counts.items()
                if count == 0
            )
        else:
            found = record["reported_found"]
            hit = record["reported_hit"]
            uncovered = []
        total_found += found
        total_hit += hit
        normalized.append({
            "path": filename,
            "lines_found": found,
            "lines_hit": hit,
            "line_coverage": _percentage(hit, found),
            "uncovered_lines": uncovered,
        })

    return {
        "name": source.stem if name is None else name,
        "format": "lcov",
        "source": str(source.resolve()),
        "lines_found": total_found,
        "lines_hit": total_hit,
        "line_coverage": _percentage(total_hit, total_found),
        "files": normalized,
    }


def load_functional_coverage(paths: Sequence[str | Path]) -> dict[str, Any] | None:
    """Load and merge one or more Toffee functional-coverage databases."""

    if not paths:
        return None
    database = CoverageDatabase.read_json(paths[0])
    for path in paths[1:]:
        database.merge(CoverageDatabase.read_json(path))
    return database.report()


def build_unified_coverage_model(
    *,
    functional: Mapping[str, Any] | None,
    line_coverage: Sequence[Mapping[str, Any]],
    title: str = "Unified Coverage Report",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the stable, renderer-independent unified report model."""

    return {
        "format": "xreactor-unified-coverage",
        "version": REPORT_SCHEMA_VERSION,
        "title": title,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        # These metrics deliberately remain independent; averaging them hides
        # holes and has no verification-methodology meaning.
        "functional": None if functional is None else dict(functional),
        "line_coverage": [dict(item) for item in line_coverage],
    }


def render_unified_coverage_html(
    model: Mapping[str, Any],
    output: str | Path,
    *,
    template: str | Path | None = None,
) -> Path:
    """Render a self-contained HTML file with no runtime dependencies."""

    template_path = (
        Path(__file__).with_name("templates") / "unified_coverage.html"
        if template is None
        else Path(template)
    )
    document = template_path.read_text(encoding="utf-8")
    title = str(model.get("title", "Unified Coverage Report"))
    payload = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    # Prevent user-controlled names from terminating the JSON script element.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    document = document.replace("@@TITLE@@", _escape_html(title))
    document = document.replace("@@REPORT_JSON@@", payload)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination


def generate_unified_coverage_report(
    *,
    functional_paths: Sequence[str | Path] = (),
    line_coverage: Sequence[tuple[str, str | Path]] = (),
    output: str | Path,
    title: str = "Unified Coverage Report",
    template: str | Path | None = None,
) -> Path:
    """Load coverage artifacts and generate one self-contained HTML report."""

    functional = load_functional_coverage(functional_paths)
    line_reports = [parse_lcov(path, name=name) for name, path in line_coverage]
    if functional is None and not line_reports:
        raise ValueError("at least one functional or line-coverage input is required")
    model = build_unified_coverage_model(
        functional=functional,
        line_coverage=line_reports,
        title=title,
    )
    return render_unified_coverage_html(model, output, template=template)


def _site_slug(value: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "item"
    return f"{readable[:48]}-{sha256(value.encode('utf-8')).hexdigest()[:8]}"


def _prepare_site(directory: Path) -> None:
    marker = directory / ".xreactor-coverage-site"
    if directory.exists() and any(directory.iterdir()):
        if not marker.is_file():
            raise FileExistsError(
                f"refusing to replace non-Toffee directory {directory}; "
                "choose an empty output directory"
            )
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    marker.write_text("toffee unified coverage site v1\n", encoding="utf-8")


def _site_page(title: str, body: str, *, root_prefix: str) -> str:
    escaped = _escape_html(title)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escaped}</title><link rel="stylesheet" href="{root_prefix}assets/site.css">
</head><body><header><nav>
<a href="{root_prefix}index.html">Overview</a>
<a href="{root_prefix}functional/index.html">Functional</a>
<a href="{root_prefix}line/index.html">RTL line</a>
</nav></header><main><h1>{escaped}</h1>{body}</main></body></html>\n"""


def _write_site_page(path: Path, title: str, body: str, *, root_prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_site_page(title, body, root_prefix=root_prefix), encoding="utf-8")


def _inject_genhtml_backlinks(provider_dir: Path, site_root: Path) -> None:
    """Add a route back into the unified report to every genhtml page."""

    overview = site_root / "index.html"
    for page in provider_dir.rglob("*.html"):
        relative = os.path.relpath(overview, page.parent).replace(os.sep, "/")
        banner = (
            '<div style="background:#18243e;color:#e8edf7;padding:8px 14px;'
            'font:14px system-ui,sans-serif;border-bottom:1px solid #405070">'
            f'<a style="color:#8dc6ff" href="{_escape_html(relative)}">'
            "← Unified coverage overview</a></div>"
        )
        document = page.read_text(encoding="utf-8")
        if "<body>" in document and "Unified coverage overview" not in document:
            page.write_text(
                document.replace("<body>", f"<body>{banner}", 1),
                encoding="utf-8",
            )


def _bar(value: float) -> str:
    level = "bad" if value < 70 else "warn" if value < 90 else "good"
    width = max(0.0, min(100.0, value))
    return (
        f'<div class="bar"><i class="{level}" style="width:{width:.3f}%"></i></div>'
    )


def _metric(label: str, value: str, detail: str, coverage: float | None = None) -> str:
    progress = "" if coverage is None else _bar(coverage)
    return (
        '<section class="card"><div class="muted">'
        f"{_escape_html(label)}</div><div class=\"metric\">{_escape_html(value)}</div>"
        f'<div class="muted">{_escape_html(detail)}</div>{progress}</section>'
    )


def _functional_group_page(group: Mapping[str, Any]) -> str:
    schema = group["schema"]
    description = schema.get("description", "")
    parts = [
        (
            f'<p class="description">{_escape_html(description)}</p>'
            if description
            else '<p class="muted">No covergroup description provided.</p>'
        ),
        '<div class="grid">',
        _metric("Coverage", f"{group['coverage']:.2f}%", f"goal {group['goal']:.2f}%", group["coverage"]),
        _metric("Samples", str(group["samples"]), f"gated {group['gated']}"),
        _metric("Schema digest", group["schema_digest"][:12], schema["name"]),
        _metric("Sampling", group.get("sampling_backend", "manual"),
                group.get("sampling_fallback") or ""),
        "</div>",
        '<p class="muted">Select a point or cross to inspect bins, matchers, thresholds and diagnostics.</p>',
    ]
    state = "accepted" if group["covered"] else "not accepted"
    parts.append(f'<p class="{ "good" if group["covered"] else "bad"}">'
                 f'Coverage acceptance: {state}. Group goal: '
                 f'{"met" if group.get("goal_met") else "not met"}; item goals: '
                 f'{"met" if group.get("items_goal_met") else "not met"}.</p>')
    illegal_hits = group.get("illegal_hits", ())
    if illegal_hits:
        parts.append(f'<div class="alert bad">{len(illegal_hits)} illegal hit(s)</div>')
    if not group.get("collection_complete", True):
        parts.append('<div class="alert bad">Coverage collection is incomplete.</div>')
    diagnostics = group.get("diagnostics")
    if diagnostics and diagnostics["collected_runs"]:
        parts.append('<details><summary>Pattern diagnostics (optional)</summary>')
        parts.append(f'<p>Collected in {diagnostics["collected_runs"]} run(s); '
                     f'not collected in {diagnostics["uncollected_runs"]} run(s). '
                     'These counters do not contribute to coverage percentages.</p>')
        fields = ("started", "completed", "failed", "expired", "aborted", "cleared",
                  "unfinished_at_close", "peak_active")
        parts.append('<table><thead><tr><th>Pattern</th>' + ''.join(
            f'<th>{name.replace("_", " ").title()}</th>' for name in fields) + '</tr></thead><tbody>')
        for pattern in diagnostics["patterns"]:
            name = ('Sampling trigger' if pattern["kind"] == "trigger" else
                    f'{pattern["point"]}.{pattern["bin"]}')
            parts.append(f'<tr><td>{_escape_html(name)}</td>' + ''.join(
                f'<td>{pattern[field]}</td>' for field in fields) + '</tr>')
        parts.append('</tbody></table></details>')

    for heading, items, directory in (
        ("Coverpoints", group["points"], "points"),
        ("Crosses", group.get("crosses", ()), "crosses"),
    ):
        definitions = {
            item["name"]: item
            for item in schema["points" if directory == "points" else "crosses"]
        }
        parts.append(f"<h2>{heading}</h2>")
        parts.append(
            '<table><thead><tr><th>Name</th><th>Meaning</th><th>Coverage</th><th>Samples</th>'
            '<th>Gated</th><th>Ignored</th><th>Unmatched</th><th>Unknown</th></tr></thead><tbody>'
        )
        for item in items:
            state = "good" if item["coverage"] + 1e-12 >= item["goal"] else "bad"
            href = f'{directory}/{_site_slug(item["name"])}.html'
            meaning = definitions[item["name"]].get("description", "")
            parts.append(
                f'<tr><td><a href="{href}">{_escape_html(item["name"])}</a></td>'
                f'<td>{_escape_html(meaning) if meaning else "—"}</td>'
                f'<td class="{state}">{item["coverage"]:.2f}%</td>'
                f'<td>{item["samples"]}</td><td>{item["gated"]}</td>'
                f'<td>{item["ignored"]}</td><td>{item["unmatched"]}</td>'
                f'<td>{item.get("unknown", 0)}</td></tr>'
            )
        parts.append("</tbody></table>")
    return "".join(parts)


def _functional_item_page(
    item: Mapping[str, Any],
    definition: Mapping[str, Any],
    *,
    kind: str,
    origins: Mapping[str, Any] | None = None,
) -> str:
    description = definition.get("description", "")
    parts = [
        '<p><a href="../index.html">← Back to covergroup</a></p>',
        (
            f'<p class="description">{_escape_html(description)}</p>'
            if description
            else f'<p class="muted">No {kind} description provided.</p>'
        ),
        '<div class="grid">',
        _metric("Coverage", f"{item['coverage']:.2f}%", f"goal {item['goal']:.2f}%", item["coverage"]),
        _metric("Samples", str(item["samples"]), f"gated {item['gated']}"),
        _metric("Diagnostics", str(item["unmatched"]),
                f"unmatched · ignored {item['ignored']} · unknown {item.get('unknown', 0)}"),
        "</div>",
    ]
    if kind == "point":
        rows = [
            (
                bin_["name"],
                "excluded" if bin_["name"] in item.get("excluded_bins", {}) else bin_["kind"],
                item["counts"].get(bin_["name"], 0),
                bin_["at_least"],
                json.dumps(bin_["matcher"], ensure_ascii=False, sort_keys=True),
                bin_["name"],
            )
            for bin_ in definition["bins"]
        ]
    else:
        at_least = definition["at_least"]
        rows = [(bin_["label"], bin_["kind"], item["counts"][bin_["id"]], at_least,
                 json.dumps(bin_["tuple"], ensure_ascii=False), bin_["id"])
                for bin_ in item.get("bins", ())]
    parts.append(
        '<table><thead><tr><th>Bin</th><th>Kind</th><th>Hits</th>'
        '<th>at_least</th><th>Status</th><th>Matcher / definition</th><th>Run evidence</th></tr></thead><tbody>'
    )
    for name, bin_kind, count, at_least, description, identity in rows:
        covered = count >= at_least
        status = bin_kind if bin_kind in ("ignore", "illegal", "default", "excluded") else (
            "covered" if covered else "uncovered"
        )
        status_class = ("good" if covered else "bad") if bin_kind == "normal" else (
            "bad" if bin_kind == "illegal" and count else "muted")
        evidence = []
        for origin_id, record in item.get("provenance", {}).get(identity, {}).items():
            origin = (origins or {}).get(origin_id, {})
            evidence.append(json.dumps({"run": origin.get("run_id", origin_id),
                                        "metadata": origin.get("metadata", {}), **record}, ensure_ascii=False))
        proof = "<br>".join(_escape_html(value) for value in evidence) or "—"
        parts.append(
            f"<tr><td><code>{_escape_html(name)}</code></td>"
            f"<td>{_escape_html(bin_kind)}</td><td>{count}</td><td>{at_least}</td>"
            f'<td class="{status_class}">{status}</td>'
            f"<td><code>{_escape_html(description)}</code></td><td>{proof}</td></tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def generate_unified_coverage_site(
    *,
    functional_paths: Sequence[str | Path] = (),
    line_coverage: Sequence[tuple[str, str | Path]] = (),
    output_dir: str | Path,
    title: str = "Unified Coverage Report",
    genhtml_command: str = "genhtml",
) -> Path:
    """Generate a navigable static site with per-group and source pages.

    The destination may be replaced only when it contains Toffee's marker,
    preventing accidental deletion of an unrelated directory.
    """

    functional = load_functional_coverage(functional_paths)
    line_reports = [parse_lcov(path, name=name) for name, path in line_coverage]
    if functional is None and not line_reports:
        raise ValueError("at least one functional or line-coverage input is required")
    root = Path(output_dir)
    _prepare_site(root)
    assets = root / "assets"
    assets.mkdir()
    shutil.copyfile(
        Path(__file__).with_name("templates") / "coverage_site.css",
        assets / "site.css",
    )

    functional_cards: list[str] = []
    if functional is not None:
        group_rows: list[str] = []
        for group in functional["groups"]:
            slug = _site_slug(group["instance"])
            relative = f"groups/{slug}/index.html"
            group_root = root / "functional" / "groups" / slug
            point_schema = {
                item["name"]: item for item in group["schema"]["points"]
            }
            cross_schema = {
                item["name"]: item for item in group["schema"].get("crosses", ())
            }
            for item in group["points"]:
                _write_site_page(
                    group_root / "points" / f'{_site_slug(item["name"])}.html',
                    f'{group["instance"]} · point · {item["name"]}',
                    _functional_item_page(
                        item, point_schema[item["name"]], kind="point", origins=group.get("origins")
                    ),
                    root_prefix="../../../../",
                )
            for item in group.get("crosses", ()):
                _write_site_page(
                    group_root / "crosses" / f'{_site_slug(item["name"])}.html',
                    f'{group["instance"]} · cross · {item["name"]}',
                    _functional_item_page(
                        item, cross_schema[item["name"]], kind="cross", origins=group.get("origins")
                    ),
                    root_prefix="../../../../",
                )
            _write_site_page(
                root / "functional" / relative,
                f"Functional · {group['instance']}",
                _functional_group_page(group),
                root_prefix="../../../",
            )
            state = "good" if group["covered"] else "bad"
            meaning = group["schema"].get("description", "")
            group_rows.append(
                f'<tr><td><a href="{relative}">{_escape_html(group["instance"])}</a></td>'
                f'<td>{_escape_html(meaning) if meaning else "—"}</td>'
                f'<td class="{state}">{group["coverage"]:.2f}%</td>'
                f'<td>{group["samples"]}</td><td>{len(group["points"])}</td>'
                f'<td>{len(group["crosses"])}</td><td>{len(group["illegal_hits"])}</td></tr>'
            )
        body = (
            '<p class="muted">Select a covergroup instance to inspect point, cross, bin, '
            'matcher, threshold and diagnostic counters.</p><table><thead><tr>'
            '<th>Instance</th><th>Meaning</th><th>Coverage</th><th>Samples</th><th>Points</th>'
            '<th>Crosses</th><th>Illegal</th></tr></thead><tbody>'
            + "".join(group_rows)
            + "</tbody></table>"
        )
        _write_site_page(
            root / "functional" / "index.html",
            "Functional coverage",
            body,
            root_prefix="../",
        )
        functional_cards.append(
            f'<a class="card link-card" href="functional/index.html"><div class="muted">Functional</div>'
            f'<div class="metric">{functional["coverage"]:.2f}%</div>'
            f'<div>{len(functional["groups"])} covergroup instance(s)</div>{_bar(functional["coverage"])}</a>'
        )
    else:
        _write_site_page(
            root / "functional" / "index.html",
            "Functional coverage",
            '<p class="muted">No functional coverage input.</p>',
            root_prefix="../",
        )

    line_rows: list[str] = []
    line_cards: list[str] = []
    for (name, tracefile), report in zip(line_coverage, line_reports):
        slug = _site_slug(name)
        provider_dir = root / "line" / slug
        command = [
            genhtml_command,
            str(Path(tracefile).resolve()),
            "--output-directory",
            str(provider_dir),
            "--title",
            f"{title} · {name}",
            "--hierarchical",
            "--show-details",
            "--show-navigation",
            "--legend",
            "--dark-mode",
            "--no-function-coverage",
            "--no-branch-coverage",
            "--parallel",
            "0",
            "--quiet",
        ]
        # Let genhtml determine its source prefix. Stripping the entire parent
        # directory breaks source lookup for a single file in LCOV 2.0.
        try:
            subprocess.run(command, check=True, text=True, capture_output=True)
        except FileNotFoundError as error:
            raise RuntimeError(
                f"{genhtml_command!r} is required for multi-page source coverage"
            ) from error
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f"genhtml failed for {name!r}: {error.stderr.strip()}"
            ) from error
        _inject_genhtml_backlinks(provider_dir, root)
        line_rows.append(
            f'<tr><td><a href="{slug}/index.html">{_escape_html(name)}</a></td>'
            f'<td>{report["line_coverage"]:.2f}%</td><td>{report["lines_hit"]}</td>'
            f'<td>{report["lines_found"]}</td><td>{len(report["files"])}</td></tr>'
        )
        line_cards.append(
            f'<a class="card link-card" href="line/{slug}/index.html"><div class="muted">'
            f'{_escape_html(name)} RTL lines</div><div class="metric">{report["line_coverage"]:.2f}%</div>'
            f'<div>{report["lines_hit"]} / {report["lines_found"]} lines</div>'
            f'{_bar(report["line_coverage"])}</a>'
        )
    line_body = (
        '<p class="muted">Select a provider, then a source directory/file to open annotated source. '
        'The source view includes execution counts and first/next uncovered navigation.</p>'
    )
    if line_rows:
        line_body += (
            '<table><thead><tr><th>Provider</th><th>Coverage</th><th>Hit</th>'
            '<th>Found</th><th>Files</th></tr></thead><tbody>'
            + "".join(line_rows)
            + "</tbody></table>"
        )
    else:
        line_body += '<p class="muted">No line coverage input.</p>'
    _write_site_page(
        root / "line" / "index.html", "RTL line coverage", line_body, root_prefix="../"
    )

    overview = (
        '<p class="alert warn">Functional coverage and RTL line coverage are independent '
        'metrics; they are deliberately not averaged.</p>'
        '<div class="grid">'
        + "".join((*functional_cards, *line_cards))
        + '</div><h2>How to investigate</h2><ol><li>Open Functional and select a '
        'covergroup instance to inspect uncovered bins and thresholds.</li><li>Open RTL line, '
        'select a provider and source file, then navigate directly between uncovered lines.</li></ol>'
    )
    _write_site_page(root / "index.html", title, overview, root_prefix="")
    return root / "index.html"


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _line_argument(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    name, path = value.split("=", 1)
    if not name or not path:
        raise argparse.ArgumentTypeError("expected non-empty NAME=PATH")
    return name, Path(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Combine Toffee functional coverage and LCOV into HTML"
    )
    parser.add_argument(
        "--functional",
        action="append",
        default=[],
        type=Path,
        help="Toffee functional-coverage JSON; repeat to merge shards",
    )
    parser.add_argument(
        "--line-coverage",
        action="append",
        default=[],
        type=_line_argument,
        metavar="NAME=PATH",
        help="LCOV tracefile labelled by simulator/run; repeat as needed",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="legacy self-contained single HTML output",
    )
    parser.add_argument(
        "--site",
        type=Path,
        help="navigable multi-page output directory with annotated source",
    )
    parser.add_argument("--title", default="Unified Coverage Report")
    parser.add_argument("--template", type=Path)
    parser.add_argument("--genhtml", default="genhtml")
    args = parser.parse_args(argv)
    if args.output is None and args.site is None:
        parser.error("at least one of --output or --site is required")
    if args.output is not None:
        generate_unified_coverage_report(
            functional_paths=args.functional,
            line_coverage=args.line_coverage,
            output=args.output,
            title=args.title,
            template=args.template,
        )
    if args.site is not None:
        generate_unified_coverage_site(
            functional_paths=args.functional,
            line_coverage=args.line_coverage,
            output_dir=args.site,
            title=args.title,
            genhtml_command=args.genhtml,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
