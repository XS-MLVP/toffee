"""Check the real v2 API and verify every marked negative fixture is rejected."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyright", nargs="+", default=["pyright"], help="Pyright executable or node plus its entry point")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = root / "tests/typing/pyright-coverage-v2.json"
    negative = root / "tests/typing/coverage_v2_negative.py"
    for label, files in (("positive", []), ("negative", [str(negative)])):
        result = subprocess.run([*args.pyright, "-p", str(config), "--outputjson", *files],
                                cwd=root, capture_output=True, text=True)
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise SystemExit(result.stderr or result.stdout)
        if label == "positive":
            if result.returncode != 0:
                raise SystemExit(result.stdout)
            print("PASS: strict positive fixture")
        else:
            expected = {index for index, line in enumerate(negative.read_text().splitlines())
                        if "# EXPECT_ERROR" in line}
            found = {entry["range"]["start"]["line"] for entry in report["generalDiagnostics"]
                     if entry["severity"] == "error" and Path(entry["file"]) == negative}
            if not expected <= found or result.returncode == 0:
                raise SystemExit(f"missing expected errors at lines {sorted(line + 1 for line in expected - found)}")
            print(f"PASS: all {len(expected)} marked negative cases rejected")


if __name__ == "__main__":
    main()
