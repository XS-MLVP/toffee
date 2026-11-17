"""Validate port descriptions without materializing another RTL view graph."""

from collections.abc import Mapping, Sequence
import json
from typing import Any


def parse_layout(source: Any, path: str) -> Mapping[str, Any]:
    if isinstance(source, (str, bytes, bytearray)):
        try:
            source = json.loads(source)
        except (ValueError, RecursionError) as error:
            raise ValueError(f"{path} invalid Layout JSON: {error}") from error
    validate_layout(source, path)
    return source


def validate_layout(layout: Any, path: str, depth: int = 0) -> int:
    """Return the declared packed width, retaining the original mapping."""
    if depth > 64:
        raise ValueError(f"{path} Layout nesting exceeds 64 levels")
    if not isinstance(layout, Mapping) or layout.get("kind") not in ("bits", "struct", "array"):
        raise ValueError(f"{path} Layout must describe bits, struct, or array")
    kind = layout["kind"]
    width = layout.get("width", 0)
    if not isinstance(width, int) or isinstance(width, bool) or width < 0:
        raise ValueError(f"{path} Layout width must be a nonnegative integer")
    packed = layout.get("packed", True)
    if not isinstance(packed, bool):
        raise ValueError(f"{path} Layout packed must be a boolean")
    if kind == "bits":
        return width or 1
    if kind == "array":
        left, right = layout.get("left"), layout.get("right")
        if any(not isinstance(bound, int) or isinstance(bound, bool) for bound in (left, right)):
            raise ValueError(f"{path} Layout array bounds must be integers")
        child = layout.get("element")
        element_width = validate_layout(child, f"{path}[]", depth + 1)
        if packed and (not child.get("packed", True)
                       or width != (abs(left - right) + 1) * element_width):
            raise ValueError(f"{path} Layout packed array width does not match its elements")
        return width if packed else 0
    fields = layout.get("fields")
    if (not isinstance(fields, Sequence) or isinstance(fields, (str, bytes, bytearray))
            or not fields):
        raise ValueError(f"{path} Layout struct fields must be a nonempty sequence")
    names = set()
    for field in fields:
        if not isinstance(field, Mapping):
            raise ValueError(f"{path} Layout struct field must be a mapping")
        name = field.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError(f"{path} Layout struct field names must be nonempty and unique")
        names.add(name)
        child = field.get("type")
        child_width = validate_layout(child, f"{path}.{name}", depth + 1)
        if packed:
            offset = field.get("offset")
            if (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
                    or not child.get("packed", True) or offset + child_width > width):
                raise ValueError(f"{path}.{name} Layout packed field exceeds its declared width")
    return width if packed else 0
