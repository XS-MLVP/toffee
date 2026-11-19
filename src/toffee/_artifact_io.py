"""Atomic JSON writes and file identities shared by report tools and the runner."""
from __future__ import annotations

import gzip
from hashlib import file_digest
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def fingerprint(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise ValueError(f"artifact is not a regular non-symlink file: {path}")
    with path.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "size": path.stat().st_size, "sha256": digest}


def write(path: Path, value: Any, *, compressed: bool = False) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    pending = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            if compressed:
                with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as stream:
                    for part in json.JSONEncoder(separators=(",", ":")).iterencode(value):
                        stream.write(part.encode())
            else:
                raw.write((json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())
            raw.flush()
            os.fsync(raw.fileno())
        pending.replace(path)
        sync_directory(path.parent)
    finally:
        pending.unlink(missing_ok=True)
    return fingerprint(path)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
