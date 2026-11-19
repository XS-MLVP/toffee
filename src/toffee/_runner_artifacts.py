"""Read artifacts from the runner's exclusively owned directory."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from ._artifact_io import fingerprint


def read_checked(row: dict[str, Any], root: Path, *, compressed: bool = False) -> Any:
    path = Path(row["path"])
    if not path.is_relative_to(root) or fingerprint(path) != row:
        raise ValueError(f"artifact identity or ownership mismatch: {path}")
    opener = gzip.open if compressed else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)
