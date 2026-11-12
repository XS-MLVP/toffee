from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import sys

import pytest


def _bootstrap_local_xspcomm() -> None:
    """Make an existing xcomm build visible without installing it globally."""

    if importlib.util.find_spec("xspcomm") is not None:
        return

    candidates: list[Path] = []
    configured = os.environ.get("XCOMM_PYTHON")
    if configured:
        candidates.extend(
            Path(item).expanduser() for item in configured.split(os.pathsep)
        )

    checkout_parent = Path(__file__).resolve().parents[2]
    candidates.append(
        checkout_parent / "picker" / "build" / "dependence" / "xcomm" / "python"
    )

    for candidate in candidates:
        if (candidate / "xspcomm" / "__init__.py").is_file():
            sys.path.insert(0, str(candidate))
            importlib.invalidate_caches()
            return


_bootstrap_local_xspcomm()


def _native_api_error() -> str | None:
    try:
        xspcomm = importlib.import_module("xspcomm")
    except ImportError as error:
        return f"cannot import xspcomm: {error}"
    if not hasattr(xspcomm.XClock, "StepHalf"):
        return "xspcomm.XClock.StepHalf is unavailable"
    if not hasattr(xspcomm, "XTriggerEngine"):
        return "xspcomm.XTriggerEngine is unavailable"
    for method in ("AcquireClock", "ReleaseClock", "CollectExpressions", "ExprNewBits", "ExprNewBool", "ExprEvalBits"):
        if not hasattr(xspcomm.XTriggerEngine, method):
            return f"xspcomm.XTriggerEngine.{method} is unavailable; use the backend pinned in .github/native-dependencies.json"
    if getattr(xspcomm.XTriggerEngine, "CombinationalVersion", lambda: 0)() != 1:
        return "xspcomm combinational protocol 1 is unavailable; rebuild the pinned native dependency"
    return None


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-xspcomm",
        action="store_true",
        help="fail collection unless the native xspcomm trigger API is available",
    )
    parser.addoption(
        "--require-xarrays",
        action="store_true",
        help="require ABI-4 xcomm with XDataArray instead of skipping array integration tests",
    )


def pytest_configure(config: pytest.Config) -> None:
    if not (config.getoption("--require-xspcomm") or config.getoption("--require-xarrays")):
        return
    error = _native_api_error()
    if error is not None:
        requested = "--require-xarrays" if config.getoption("--require-xarrays") else "--require-xspcomm"
        raise pytest.UsageError(
            f"{requested} requested, but {error}. "
            "Install xcomm with BUILD_XSPCOMM_SWIG=python and "
            "XSPCOMM_BUILD_WHEEL=1, or set XCOMM_PYTHON to an existing "
            "xcomm build/python directory."
        )
    if config.getoption("--require-xarrays"):
        xspcomm = importlib.import_module("xspcomm")
        if not hasattr(xspcomm, "XDataArray") or xspcomm.abi_version() != 4:
            raise pytest.UsageError("--require-xarrays requires xcomm XDataArray and matching native ABI-4 Python bindings")
