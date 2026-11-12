"""Bounded views over independent signal leaves, not slices of a packed bus."""

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


def _is_native_array(source: Any) -> bool:
    return all(callable(getattr(source, name, None))
               for name in ("Left", "Right", "Shape", "At"))


def _is_indexed_view(source: Any) -> bool:
    # Do not depend on Picker's private Python class, import xspcomm, or mistake
    # an arbitrary iterable / XData bit selector for an unpacked signal array.
    return (hasattr(source, "bounds") and hasattr(source, "shape")
            and hasattr(source, "indices")
            and callable(getattr(source, "__getitem__", None)))


class SignalArray:
    """A read-only array structure using its source's actual index bounds.

    Sources may be Picker-style views exposing bounds/shape/indices/indexing,
    or xcomm XDataArray views exposing Left/Right/Shape/At. No signal is copied,
    rebound, registered with a clock, or read while constructing this adapter.
    Internal native views retain their storage coordinates, not guessed RTL bounds.
    """

    __slots__ = ("_source", "_bounds", "_native", "_prefix", "_children", "_path")

    def __init__(self, source: Any, *, name: str = "array") -> None:
        if isinstance(source, ArrayValue):
            raise TypeError("array snapshots are values, not live signal sources")
        native = _is_native_array(source)
        if native:
            left, right = tuple(source.Left()), tuple(source.Right())
            if len(left) != len(right):
                raise ValueError("array left/right rank mismatch")
            bounds = _normalize_bounds(tuple(zip(left, right)))
            shape = tuple(source.Shape())
        elif _is_indexed_view(source):
            bounds = _normalize_bounds(source.bounds)
            if not isinstance(source.shape, Sequence):
                raise TypeError(f"{name} array shape must be a sequence")
            shape = tuple(source.shape)
        else:
            raise TypeError("SignalArray requires a bounded signal view or XDataArray")
        if shape != _shape(bounds):
            raise ValueError(f"{name} array shape does not match its bounds")
        self._source = source
        self._bounds = bounds
        self._native = native
        self._prefix: tuple[int, ...] = ()
        self._children: dict[int, Any] = {}
        self._path = name

    @classmethod
    def _native_child(cls, parent: SignalArray, index: int) -> SignalArray:
        child = object.__new__(cls)
        child._source = parent._source
        child._bounds = parent._bounds[1:]
        child._native = True
        child._prefix = parent._prefix + (index,)
        child._children = {}
        child._path = f"{parent._path}[{index}]"
        return child

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
            if self._native:
                child = (self._native_child(self, index) if len(self._bounds) > 1
                         else self._source.At(list(self._prefix + (index,))))
            else:
                child = self._source[index]
                if len(self._bounds) > 1:
                    child = _as_signal_array(child, name=path)
                    if child is None or child.bounds != self._bounds[1:]:
                        raise ValueError(f"{path} array bounds do not match its parent")
            if len(self._bounds) == 1:
                if (_is_native_array(child) or _is_indexed_view(child)
                        or not any(hasattr(child, name)
                                   for name in ("U", "W", "Set", "value", "xdata"))):
                    raise TypeError(f"{path} must be a signal leaf")
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
    if _is_native_array(source) or _is_indexed_view(source):
        return SignalArray(source, name=name)
    return None


@dataclass(frozen=True, slots=True)
class ArrayValue:
    """An immutable bounded array snapshot with LogicValue leaves.

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
            elif not isinstance(child, LogicValue):
                raise TypeError("array snapshot leaves must be LogicValue")
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
        return {index: (child.as_dict() if isinstance(child, ArrayValue)
                        else child.as_int())
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
