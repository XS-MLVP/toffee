"""Bounded signal trees using the coordinates and storage of their source view."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from operator import index as integer_index
from typing import Any

from .events import LogicValue

Bounds = tuple[tuple[int, int], ...]


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError("array indices and bounds must be integers, not bool")
    try:
        return integer_index(value)
    except TypeError as error:
        raise TypeError("array indices and bounds must be integers") from error


def _normalize_bounds(bounds: Any) -> Bounds:
    if (not isinstance(bounds, Sequence)
            or isinstance(bounds, (str, bytes, bytearray))):
        raise TypeError("array bounds must be a nonempty sequence of pairs")
    if any(not isinstance(pair, Sequence) or isinstance(pair, (str, bytes, bytearray))
           for pair in bounds):
        raise TypeError("array bounds must be a nonempty sequence of pairs")
    result = tuple(tuple(pair) for pair in bounds)
    if not result or any(len(pair) != 2 for pair in result):
        raise ValueError("array bounds must be a nonempty sequence of pairs")
    return tuple((_integer(left), _integer(right)) for left, right in result)


def _shape(bounds: Bounds) -> tuple[int, ...]:
    return tuple(abs(left - right) + 1 for left, right in bounds)


def _indices(bounds: Bounds) -> range:
    left, right = bounds[0]
    step = 1 if left <= right else -1
    return range(left, right + step, step)


def _checked_index(bounds: Bounds, value: Any) -> tuple[int, int]:
    index = _integer(value)
    left, right = bounds[0]
    position = (index - left) * (1 if left <= right else -1)
    if not 0 <= position < abs(left - right) + 1:
        raise IndexError(f"array index {index} outside [{left}:{right}]")
    return index, position


def _is_indexed_view(source: Any) -> bool:
    # Do not depend on Picker's private Python class, import xspcomm, or mistake
    # an arbitrary iterable / XData bit selector for an unpacked signal array.
    return (hasattr(source, "bounds") and hasattr(source, "shape")
            and hasattr(source, "indices")
            and callable(getattr(source, "__getitem__", None)))


class SignalArray:
    """A read-only array structure using its source's actual index bounds.

    Sources expose the common XArray bounds/shape/indices/indexing protocol.
    Elements may be structs.
    No signal is copied,
    rebound, registered with a clock, or read while constructing this adapter.
    Internal native views retain their storage coordinates, not guessed RTL bounds.
    """

    __slots__ = ("_source", "_bounds", "_children", "_path")

    def __init__(self, source: Any, *, name: str = "array") -> None:
        if isinstance(source, ArrayValue):
            raise TypeError("array snapshots are values, not live signal sources")
        if _is_indexed_view(source):
            bounds = _normalize_bounds(source.bounds)
            if not isinstance(source.shape, Sequence):
                raise TypeError(f"{name} array shape must be a sequence")
            shape = tuple(source.shape)
        else:
            raise TypeError("SignalArray requires a bounded signal view")
        if shape != _shape(bounds):
            raise ValueError(f"{name} array shape does not match its bounds")
        self._source = source
        self._bounds = bounds
        self._children: dict[int, Any] = {}
        self._path = name

    @property
    def bounds(self) -> Bounds:
        return self._bounds

    @property
    def shape(self) -> tuple[int, ...]:
        return _shape(self._bounds)

    @property
    def indices(self) -> range:
        return _indices(self._bounds)

    def __len__(self) -> int:
        return self.shape[0]

    def __iter__(self) -> Iterator[Any]:
        return (self[index] for index in self.indices)

    def __getitem__(self, key: int) -> Any:
        index, _ = _checked_index(self._bounds, key)
        if index not in self._children:
            path = f"{self._path}[{index}]"
            child = self._source[index]
            if len(self._bounds) > 1:
                child = _as_signal_array(child, name=path)
                if child is None or child.bounds != self._bounds[1:]:
                    raise ValueError(f"{path} array bounds do not match its parent")
            if len(self._bounds) == 1:
                from .data import Bundle, _normalize_node
                child = _normalize_node(child, path)
                if not isinstance(child, Bundle) and not any(
                    hasattr(child, name) for name in ("U", "W", "Set", "value", "xdata")
                ):
                    raise TypeError(f"{path} must be a signal or struct leaf")
            self._children[index] = child
        return self._children[index]

    def leaves(self) -> Iterator[tuple[str, Any]]:
        from .data import iter_data_leaves
        yield from iter_data_leaves(self, self._path)

    def sample(self) -> ArrayValue:
        from .data import sample_data
        return sample_data(self)

    def compile_sampler(self):
        from .data import compile_sampler
        return compile_sampler(self)

    def drive(self, value: Any) -> None:
        from .data import drive_data
        drive_data(self, value, self._path)


def _as_signal_array(source: Any, *, name: str) -> SignalArray | None:
    if isinstance(source, ArrayValue):
        raise TypeError(f"{name} array snapshot is not a live signal source")
    if isinstance(source, SignalArray):
        return source
    if _is_indexed_view(source):
        return SignalArray(source, name=name)
    return None


@dataclass(frozen=True, slots=True)
class ArrayValue:
    """An immutable bounded array snapshot with LogicValue or struct elements.

    Iteration follows declaration order. Integer indexing always uses actual
    bounds, including negative indices; this is deliberately not a Sequence.
    """

    bounds: Bounds
    _items: tuple[Any, ...]

    def __post_init__(self) -> None:
        bounds = _normalize_bounds(self.bounds)
        items = tuple(self._items)
        if len(items) != _shape(bounds)[0]:
            raise ValueError("array snapshot length does not match its bounds")
        for child in items:
            if len(bounds) > 1:
                if not isinstance(child, ArrayValue) or child.bounds != bounds[1:]:
                    raise ValueError("array snapshot child bounds do not match its parent")
            else:
                from .data import BundleValue
                if not isinstance(child, (LogicValue, BundleValue)):
                    raise TypeError("array snapshot leaves must be LogicValue or BundleValue")
        object.__setattr__(self, "bounds", bounds)
        object.__setattr__(self, "_items", items)

    @property
    def shape(self) -> tuple[int, ...]:
        return _shape(self.bounds)

    @property
    def indices(self) -> range:
        return _indices(self.bounds)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._items)

    def __getitem__(self, key: int) -> Any:
        _, position = _checked_index(self.bounds, key)
        return self._items[position]

    def as_dict(self) -> dict[int, Any]:
        """Return plain integers under actual index keys; reject unknown bits."""
        from .data import _value_as_plain
        return {index: _value_as_plain(child)
                 for index, child in zip(self.indices, self._items)}

    def as_tuple(self) -> tuple[Any, ...]:
        """Explicitly discard bounds, retaining immutable four-state leaves."""
        return tuple(child.as_tuple() if isinstance(child, ArrayValue) else child
                     for child in self._items)


def _array_drive_items(target: SignalArray, value: Any, path: str) -> tuple[Any, ...]:
    if isinstance(value, ArrayValue):
        if value.bounds != target.bounds:
            raise ValueError(f"{path} array bounds mismatch: expected={target.bounds}, actual={value.bounds}")
        return tuple(value)
    if isinstance(value, Mapping):
        if any(isinstance(key, bool) or not isinstance(key, int) for key in value):
            raise TypeError(f"{path} array mapping keys must be integer indices")
        missing = tuple(index for index in target.indices if index not in value)
        extra = tuple(index for index in value if index not in target.indices)
        if missing or extra:
            raise ValueError(f"{path} array indices mismatch: missing={missing}, extra={extra}")
        return tuple(value[index] for index in target.indices)
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray))
            or len(value) != len(target)):
        raise ValueError(f"{path} requires {len(target)} values in declaration order")
    return tuple(value)
