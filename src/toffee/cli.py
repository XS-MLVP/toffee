"""The Toffee test runner; pytest and xdist remain execution backends."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.util import find_spec
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="toffee")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run tests and collect coverage automatically")
    run.add_argument("--workers", "-j", type=int, default=1)
    run.add_argument("--output", type=Path)
    run.add_argument("--keep-coverage-inputs", action="store_true")
    run.add_argument("--export-coverage-json", action="store_true")
    run.add_argument("--native-coverage-tool", help="override the Verilator coverage processor")
    args, forwarded = parser.parse_known_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    if find_spec("pytest") is None or args.workers > 1 and find_spec("xdist") is None:
        parser.error('install the runner dependencies with pip install "uc-toffee[runner]"')
    if any(value.startswith(("-n", "--numprocesses", "--dist", "--tx", "--max-worker-restart"))
           for value in forwarded):
        parser.error("use --workers to select parallelism; the runner owns worker scheduling and restart policy")
    output = (args.output or Path("output") / ("toffee-" +
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])).resolve()
    if output.exists():
        parser.error(f"output must be a new directory: {output}")
    command = [sys.executable, "-m", "pytest", "-p", "toffee._pytest",
               f"--toffee-output={output}"]
    if args.workers > 1:
        command += ["-p", "xdist", "-n", str(args.workers), "--dist=load",
                    "--max-worker-restart=0"]
    if args.keep_coverage_inputs:
        command.append("--toffee-keep-coverage-inputs")
    if args.export_coverage_json:
        command.append("--toffee-export-coverage-json")
    if args.native_coverage_tool:
        command.append(f"--toffee-native-coverage-tool={args.native_coverage_tool}")
    command += forwarded[1:] if forwarded[:1] == ["--"] else forwarded
    env = os.environ.copy()
    # Also support python -m toffee from an uninstalled source checkout.
    source = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (source, env.get("PYTHONPATH"))))
    try:
        return subprocess.call(command, env=env)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
