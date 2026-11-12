"""Static attribute/index paths shared by data binding and trigger expressions."""

from __future__ import annotations

from typing import Any

SignalPath = tuple[str | int, ...]


def validate_path(path: SignalPath) -> None:
    if not isinstance(path, tuple) or not path:
        raise ValueError("signal path must be a nonempty tuple")
    if any(not ((isinstance(part, str) and part) or type(part) is int)
           for part in path):
        raise TypeError("signal path parts must be nonempty attributes or integer indices")


def resolve_path(root: Any, path: SignalPath) -> Any:
    value = root
    for part in path:
        if type(part) is int:
            if callable(getattr(value, "W", None)):
                raise TypeError("integer signal paths select array elements, not packed signal bits; use an explicit packed view")
            value = value[part]
        else:
            value = getattr(value, part)
    return value


def format_path(path: SignalPath) -> str:
    result = ""
    for part in path:
        if type(part) is int:
            result += f"[{part}]"
        else:
            result += ("." if result else "") + part
    return result


def path_sort_key(path: SignalPath) -> tuple[tuple[int, Any], ...]:
    # Mixed attribute/index paths must not ask Python to compare str with int.
    return tuple((1, part) if type(part) is int else (0, part) for part in path)
