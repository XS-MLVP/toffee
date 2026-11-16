from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from keyword import iskeyword
from types import MappingProxyType
from typing import Any

from .events import LogicValue
from .signals import (
    as_xdata,
    read_signal,
    sample_signal,
    signal_width,
    write_signal,
)
from .signals import compile_signal_sampler

DataNode = Any
ValueNode = Any

_MAX_LAYOUT_DEPTH = 64


@dataclass(frozen=True, slots=True)
class Field:
    source: Any
    width: int | None = None

    def __post_init__(self) -> None:
        if self.width is not None and self.width <= 0:
            raise ValueError("Field width must be positive")


@dataclass(frozen=True, slots=True)
class PortField:
    """One named member of a Picker port layout.

    ``offset`` is a **bit** offset from the least-significant bit of the
    enclosing packed struct, not a byte offset and not a bit range from the
    most-significant end. Picker emits it in ``Layout.fields[].offset`` and
    ``xcomm::detail::ViewNode::Child()`` adds it directly to the child's LSB
    (``view.cpp``: ``child_offset += layout->offsets.at(position)``). A field
    declared first therefore gets the offset after all fields declared later,
    e.g. ``struct packed { logic [0:4] tag; bit valid; }`` emits
    ``tag.offset == 1`` and ``valid.offset == 0``.

    Unpacked structs have no bit placement; Picker omits ``offset`` for their
    fields and exposes each member through a separate container child.
    """

    name: str
    offset: int | None
    type: "PortLayout"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("port layout field name must be a non-empty string")
        if self.offset is not None and self.offset < 0:
            raise ValueError("port layout field offset cannot be negative")

    @property
    def width(self) -> int:
        return self.type.width

    @property
    def signed(self) -> bool:
        return self.type.signed


@dataclass(frozen=True, slots=True)
class PortLayout:
    """Recursive type layout emitted by Picker for one port.

    ``kind`` is ``"bits"``, ``"array"`` or ``"struct"``. Picker represents
    every packed vector dimension as a nested ``array`` node whose ``width``
    is already the *expanded* width of that dimension; the innermost
    ``bits`` node has ``width == 0`` because it denotes a one-bit scalar
    primitive. Both facts are normalized here: ``bits`` width is at least 1,
    and a packed array's width is the total bit width of the dimension.

    Packed layouts can be materialized as a :class:`PackedView`; unpacked
    layouts describe the structure of an XArray/XStruct container.
    """

    kind: str
    packed: bool
    width: int
    signed: bool = False
    four_state: bool = True
    left: int | None = None
    right: int | None = None
    element: "PortLayout | None" = None
    fields: tuple[PortField, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"array", "bits", "struct"}:
            raise ValueError(
                f"unsupported aggregate metadata: layout kind {self.kind!r}"
            )
        if self.width < 0:
            raise ValueError("port layout width cannot be negative")
        if self.kind == "array":
            if self.element is None:
                raise ValueError(
                    "unsupported aggregate metadata: array layout requires "
                    "an element layout"
                )
            if self.left is None or self.right is None:
                raise ValueError(
                    "unsupported aggregate metadata: array layout requires "
                    "left and right bounds"
                )
            if self.fields:
                raise ValueError("array layout cannot declare fields")
            if self.packed and self.width != self.count * self.element.width:
                raise ValueError(
                    "unsupported aggregate metadata: packed array width "
                    f"{self.width} does not match {self.count} elements of "
                    f"{self.element.width} bits"
                )
        elif self.kind == "struct":
            if not self.fields:
                raise ValueError(
                    "unsupported aggregate metadata: struct layout requires "
                    "at least one field"
                )
            if self.element is not None:
                raise ValueError("struct layout cannot declare an element")
            if self.packed:
                for field in self.fields:
                    if field.offset is None:
                        raise ValueError(
                            "unsupported aggregate metadata: packed struct "
                            f"field {field.name!r} has no offset"
                        )
                    if field.offset + field.width > self.width:
                        raise ValueError(
                            "unsupported aggregate metadata: packed struct "
                            f"field {field.name!r} exceeds width {self.width}"
                        )
        else:
            if self.element is not None or self.fields:
                raise ValueError("bits layout cannot declare element or fields")

    @property
    def count(self) -> int:
        if self.kind != "array":
            raise AttributeError("only array layouts have a count")
        assert self.left is not None and self.right is not None
        return abs(self.left - self.right) + 1

    @property
    def indices(self) -> tuple[int, ...]:
        if self.kind != "array":
            raise AttributeError("only array layouts have indices")
        assert self.left is not None and self.right is not None
        step = 1 if self.left <= self.right else -1
        return tuple(range(self.left, self.right + step, step))

    @classmethod
    def from_json(cls, source: Any) -> "PortLayout":
        """Parse Picker's ``Layout`` field.

        Picker emits a JSON object in current builds; older or wrapped
        metadata can carry the same document as a string. Both forms are
        accepted here. Parsing is depth-limited because a truncated or
        malicious document must not turn into unbounded Python recursion.
        """
        if isinstance(source, PortLayout):
            return source
        if isinstance(source, Mapping):
            data = source
        elif isinstance(source, (str, bytes, bytearray)):
            try:
                data = json.loads(source)
            except (TypeError, ValueError, RecursionError) as error:
                raise ValueError(
                    "unsupported aggregate metadata: invalid Layout JSON: "
                    f"{error}"
                ) from error
        else:
            raise TypeError(
                "Picker Layout must be a mapping or a JSON string, got "
                f"{type(source).__name__}"
            )
        if not isinstance(data, Mapping):
            raise ValueError(
                "unsupported aggregate metadata: Layout must be a JSON object"
            )
        try:
            return cls._from_mapping(data, "layout", 0)
        except RecursionError as error:
            raise ValueError(
                "unsupported aggregate metadata: layout nesting exceeds "
                f"{_MAX_LAYOUT_DEPTH} levels"
            ) from error

    @classmethod
    def _from_mapping(
        cls,
        data: Mapping[str, Any],
        path: str,
        depth: int,
    ) -> "PortLayout":
        if depth > _MAX_LAYOUT_DEPTH:
            raise ValueError(
                "unsupported aggregate metadata: layout nesting exceeds "
                f"{_MAX_LAYOUT_DEPTH} levels at {path}"
            )
        kind = data.get("kind")
        if kind not in {"array", "bits", "struct"}:
            raise ValueError(
                "unsupported aggregate metadata: unknown layout kind "
                f"{kind!r} at {path}"
            )
        packed = bool(data.get("packed", True))
        signed = bool(data.get("signed", False))
        four_state = bool(data.get("four_state", True))
        raw_width = data.get("width", 0)
        if not isinstance(raw_width, int) or isinstance(raw_width, bool):
            raise ValueError(
                f"{path} layout width must be an integer, got {raw_width!r}"
            )
        if raw_width < 0:
            raise ValueError(f"{path} layout width cannot be negative")

        if kind == "bits":
            return cls(
                kind="bits",
                packed=packed,
                width=max(1, raw_width),
                signed=signed,
                four_state=four_state,
            )

        if kind == "array":
            if "left" not in data or "right" not in data:
                raise ValueError(
                    "unsupported aggregate metadata: array layout requires "
                    f"left and right bounds at {path}"
                )
            left = data["left"]
            right = data["right"]
            if (
                not isinstance(left, int)
                or isinstance(left, bool)
                or not isinstance(right, int)
                or isinstance(right, bool)
            ):
                raise ValueError(f"{path} array bounds must be integers")
            element_data = data.get("element")
            if not isinstance(element_data, Mapping):
                raise ValueError(
                    "unsupported aggregate metadata: array layout requires "
                    f"an element object at {path}"
                )
            element = cls._from_mapping(element_data, f"{path}[]", depth + 1)
            count = abs(left - right) + 1
            # Picker's array width is the expanded width of this dimension.
            # ``element.width`` is one element's width, so both forms agree
            # for well-formed metadata. Prefer the emitted total (the
            # authoritative value) and let validation catch disagreements.
            width = (
                raw_width if packed and raw_width > 0
                else count * element.width if packed
                else 0
            )
            return cls(
                kind="array",
                packed=packed,
                width=width,
                signed=signed,
                four_state=four_state,
                left=left,
                right=right,
                element=element,
            )

        raw_fields = data.get("fields")
        if not _is_sequence(raw_fields) or not raw_fields:
            raise ValueError(
                "unsupported aggregate metadata: struct layout requires a "
                f"non-empty fields list at {path}"
            )
        fields: list[PortField] = []
        for index, raw_field in enumerate(raw_fields):
            if not isinstance(raw_field, Mapping):
                raise ValueError(
                    f"{path} struct field {index} must be an object"
                )
            name = raw_field.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(
                    f"{path} struct field {index} has no valid name"
                )
            field_data = raw_field.get("type")
            if not isinstance(field_data, Mapping):
                raise ValueError(
                    "unsupported aggregate metadata: struct field "
                    f"{name!r} has no type object at {path}"
                )
            field_layout = cls._from_mapping(
                field_data, f"{path}.{name}", depth + 1
            )
            raw_offset = raw_field.get("offset")
            if raw_offset is not None and (
                not isinstance(raw_offset, int) or isinstance(raw_offset, bool)
            ):
                raise ValueError(
                    f"{path} struct field {name!r} offset must be an integer"
                )
            fields.append(PortField(name, raw_offset, field_layout))

        names = [field.name for field in fields]
        if len(set(names)) != len(names):
            raise ValueError(
                f"{path} struct layout field names must be unique"
            )
        if packed:
            offsets = [field.offset for field in fields]
            if any(offset is None for offset in offsets):
                missing = names[offsets.index(None)]
                raise ValueError(
                    "unsupported aggregate metadata: packed struct field "
                    f"{missing!r} has no offset at {path}"
                )
            extent = 0
            for field in fields:
                assert field.offset is not None
                extent = max(extent, field.offset + field.width)
            width = raw_width or extent
            if width < extent:
                raise ValueError(
                    "unsupported aggregate metadata: packed struct width "
                    f"{width} is too small for its fields at {path}"
                )
        else:
            width = 0
        return cls(
            kind="struct",
            packed=packed,
            width=width,
            signed=signed,
            four_state=four_state,
            fields=tuple(fields),
        )


class PackedLayout:
    """Declarative recursive layout for one packed XData signal."""

    offset: int | None
    _reserved_fields = frozenset(
        {"drive", "fields", "kind", "layout", "leaves", "parent", "sample"}
    )

    @property
    def width(self) -> int:
        raise NotImplementedError

    @classmethod
    def bits(cls, width: int, *, offset: int | None = None) -> "PackedLayout":
        return _PackedBits(width, _packed_offset(offset))

    @classmethod
    def array(
        cls,
        count: int,
        element: "PackedLayout",
        *,
        lsb_first: bool = True,
        stride: int | None = None,
        offset: int | None = None,
        left: int | None = None,
        right: int | None = None,
    ) -> "PackedLayout":
        if not isinstance(element, PackedLayout):
            raise TypeError("PackedLayout.array element must be a PackedLayout")
        if element.offset is not None:
            raise ValueError(
                "array element layout cannot have an offset; use array stride "
                "or place the array itself"
            )
        if count <= 0:
            raise ValueError("PackedLayout.array count must be positive")
        if (left is None) != (right is None):
            raise ValueError(
                "PackedLayout.array requires both left and right or neither"
            )
        if left is not None and right is not None:
            if (
                not isinstance(left, int)
                or isinstance(left, bool)
                or not isinstance(right, int)
                or isinstance(right, bool)
            ):
                raise ValueError("PackedLayout.array bounds must be integers")
            if abs(left - right) + 1 != count:
                raise ValueError(
                    f"PackedLayout.array count {count} does not match RTL "
                    f"bounds [{left}:{right}]"
                )
        if stride is None:
            stride = element.width
        if stride < element.width:
            raise ValueError(
                "PackedLayout.array stride cannot be smaller than element width"
            )
        return _PackedArrayLayout(
            count,
            element,
            bool(lsb_first),
            stride,
            _packed_offset(offset),
            left,
            right,
        )

    @classmethod
    def struct(
        cls,
        fields: Mapping[str, "PackedLayout"],
        *,
        width: int | None = None,
        lsb_first: bool = True,
        allow_overlap: bool = False,
        offset: int | None = None,
    ) -> "PackedLayout":
        if not fields:
            raise ValueError("PackedLayout.struct requires at least one field")
        entries: list[tuple[str, PackedLayout]] = []
        for name, layout in fields.items():
            if (
                not isinstance(name, str)
                or not name.isidentifier()
                or iskeyword(name)
                or name.startswith("_")
                or name in cls._reserved_fields
            ):
                raise ValueError(f"invalid packed field name: {name!r}")
            if not isinstance(layout, PackedLayout):
                raise TypeError(
                    f"packed field {name!r} must be a PackedLayout"
                )
            entries.append((name, layout))

        if width is not None and width <= 0:
            raise ValueError("PackedLayout.struct width must be positive")
        explicit_extent = max(
            (
                layout.offset + layout.width
                for _, layout in entries
                if layout.offset is not None
            ),
            default=0,
        )
        automatic_width = sum(
            layout.width for _, layout in entries if layout.offset is None
        )
        has_explicit = any(layout.offset is not None for _, layout in entries)
        has_automatic = any(layout.offset is None for _, layout in entries)
        if has_explicit and has_automatic:
            raise ValueError(
                "PackedLayout.struct cannot mix explicit and automatic "
                "field offsets"
            )
        if width is None:
            width = max(explicit_extent, automatic_width)
        if width < explicit_extent or width < automatic_width:
            raise ValueError(
                f"PackedLayout.struct width {width} is too small for its fields"
            )

        placements: list[tuple[str, PackedLayout, int]] = []
        cursor = 0 if lsb_first else width
        for name, layout in entries:
            if layout.offset is not None:
                placement = layout.offset
            elif lsb_first:
                placement = cursor
                cursor += layout.width
            else:
                cursor -= layout.width
                placement = cursor
            if placement < 0 or placement + layout.width > width:
                raise ValueError(
                    f"packed field {name!r} range "
                    f"[{placement}, {placement + layout.width}) exceeds "
                    f"struct width {width}"
                )
            placements.append((name, layout, placement))

        if not allow_overlap:
            ordered = sorted(
                (placement, placement + layout.width, name)
                for name, layout, placement in placements
            )
            for (_, previous_end, previous_name), (
                current_start,
                _,
                current_name,
            ) in zip(ordered, ordered[1:]):
                if current_start < previous_end:
                    raise ValueError(
                        f"packed fields {previous_name!r} and "
                        f"{current_name!r} overlap"
                    )

        return _PackedStructLayout(
            tuple(placements),
            width,
            bool(lsb_first),
            bool(allow_overlap),
            _packed_offset(offset),
        )


def _packed_offset(offset: int | None) -> int | None:
    if offset is not None and offset < 0:
        raise ValueError("packed layout offset cannot be negative")
    return offset


@dataclass(frozen=True, slots=True)
class _PackedBits(PackedLayout):
    bit_width: int
    offset: int | None = None

    def __post_init__(self) -> None:
        if self.bit_width <= 0:
            raise ValueError("PackedLayout.bits width must be positive")

    @property
    def width(self) -> int:
        return self.bit_width


@dataclass(frozen=True, slots=True)
class _PackedArrayLayout(PackedLayout):
    count: int
    element: PackedLayout
    lsb_first: bool
    stride: int
    offset: int | None = None
    left: int | None = None
    right: int | None = None

    @property
    def width(self) -> int:
        return (self.count - 1) * self.stride + self.element.width

    @property
    def indices(self) -> tuple[int, ...]:
        """Declaration-order RTL indices for this dimension.

        Without explicit bounds the historical positional indices
        ``0..count-1`` are returned. With bounds, the left index is the
        most-significant RTL element for a packed dimension; ``PackedView``
        maps these to physical bit positions.
        """
        if self.left is None:
            return tuple(range(self.count))
        assert self.right is not None
        step = 1 if self.left <= self.right else -1
        return tuple(range(self.left, self.right + step, step))


@dataclass(frozen=True, slots=True)
class _PackedStructLayout(PackedLayout):
    placements: tuple[tuple[str, PackedLayout, int], ...]
    struct_width: int
    lsb_first: bool
    allow_overlap: bool
    offset: int | None = None

    @property
    def width(self) -> int:
        return self.struct_width


class PackedView:
    """Live recursive view whose leaves directly slice one root XData.

    Arrays preserve Picker's RTL declaration order: for a packed dimension
    ``[2:0]`` the left index (2) is the most-significant element. Integer
    indexing uses RTL indices when the layout carries bounds; otherwise the
    historical positional ``0..count-1`` indices are used.
    """

    __slots__ = ("_parent", "_layout", "_kind", "_children", "_indices", "_path")

    def __init__(
        self,
        signal: Any,
        layout: PackedLayout,
        *,
        name: str = "packed",
        strict_width: bool = True,
    ) -> None:
        if not isinstance(layout, PackedLayout):
            raise TypeError("PackedView layout must be a PackedLayout")
        if isinstance(layout, _PackedBits):
            raise ValueError("PackedView root layout must be an array or struct")
        parent = as_xdata(signal)
        sub_data_ref = getattr(parent, "SubDataRef", None)
        if not callable(sub_data_ref):
            raise TypeError("PackedView signal must provide SubDataRef")
        root_offset = layout.offset or 0
        required_width = root_offset + layout.width
        actual_width = signal_width(parent)
        if strict_width and required_width != actual_width:
            raise ValueError(
                f"PackedView layout uses {required_width} bits, but signal "
                f"width is {actual_width}"
            )
        if required_width > actual_width:
            raise ValueError(
                f"PackedView layout uses {required_width} bits, but signal "
                f"width is {actual_width}"
            )
        self._parent = parent
        self._layout = layout
        self._path = name
        self._init_composite(layout, root_offset, name)

    @classmethod
    def _nested(
        cls, parent: Any, layout: PackedLayout, base: int, path: str
    ) -> "PackedView":
        view = object.__new__(cls)
        view._parent = parent
        view._layout = layout
        view._path = path
        view._init_composite(layout, base, path)
        return view

    @staticmethod
    def _build_node(
        parent: Any, layout: PackedLayout, base: int, path: str
    ) -> Any:
        if isinstance(layout, _PackedBits):
            return parent.SubDataRef(base, layout.width, path)
        return PackedView._nested(parent, layout, base, path)

    def _init_composite(
        self, layout: PackedLayout, base: int, path: str
    ) -> None:
        if isinstance(layout, _PackedArrayLayout):
            self._kind = "array"
            declaration = layout.indices
            has_rtl_bounds = layout.left is not None
            items = []
            indices = []
            for position in range(layout.count):
                physical = (
                    position if layout.lsb_first
                    else layout.count - 1 - position
                )
                index = (
                    declaration[
                        position if not layout.lsb_first
                        else layout.count - 1 - position
                    ]
                    if has_rtl_bounds
                    else position
                )
                child_base = base + physical * layout.stride
                items.append(
                    self._build_node(
                        self._parent,
                        layout.element,
                        child_base,
                        f"{path}[{index}]",
                    )
                )
                indices.append(index)
            self._children = tuple(items)
            self._indices = tuple(indices)
            return
        if isinstance(layout, _PackedStructLayout):
            self._kind = "struct"
            children = {
                name: self._build_node(
                    self._parent,
                    child,
                    base + placement,
                    f"{path}.{name}",
                )
                for name, child, placement in layout.placements
            }
            self._children = MappingProxyType(children)
            self._indices = ()
            return
        raise TypeError(f"unsupported packed layout: {type(layout).__name__}")

    @property
    def parent(self) -> Any:
        return self._parent

    @property
    def layout(self) -> PackedLayout:
        return self._layout

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def indices(self) -> tuple[int, ...]:
        """RTL indices in the same order as ``__iter__``/``sample``."""
        if self._kind != "array":
            raise AttributeError("struct PackedView has no indices")
        return self._indices

    @property
    def bounds(self) -> tuple[tuple[int, int], ...]:
        if self._kind != "array":
            raise AttributeError("struct PackedView has no bounds")
        layout = self._layout
        assert isinstance(layout, _PackedArrayLayout)
        if layout.left is None:
            assert layout.right is None
            return ((0, layout.count - 1),)
        assert layout.right is not None
        return ((layout.left, layout.right),)

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(
            abs(left - right) + 1 for left, right in self.bounds
        )

    @property
    def fields(self) -> Mapping[str, Any]:
        if self._kind != "struct":
            raise AttributeError("array PackedView has no fields")
        return self._children

    def _array_position(self, key: int) -> int:
        assert self._kind == "array"
        try:
            return self._indices.index(key)
        except ValueError:
            layout = self._layout
            assert isinstance(layout, _PackedArrayLayout)
            if layout.left is None and -len(self._children) <= key < 0:
                return len(self._children) + key
            raise IndexError(
                f"{self._path} RTL array index {key} not in "
                f"{self._indices}"
            ) from None

    def __getitem__(self, key: int | slice | str) -> Any:
        if self._kind == "array" and isinstance(key, int) and not isinstance(
            key, bool
        ):
            return self._children[self._array_position(key)]
        return self._children[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._children)

    def __len__(self) -> int:
        return len(self._children)

    def __getattr__(self, name: str) -> Any:
        if self._kind == "struct":
            try:
                return self._children[name]
            except KeyError as error:
                raise AttributeError(name) from error
        raise AttributeError(name)

    def leaves(self) -> Iterator[tuple[str, Any]]:
        yield from _iter_leaves(self, self._path)

    def sample(self) -> ValueNode:
        if self._kind == "struct":
            return BundleValue(
                tuple(
                    (name, _sample_node(child))
                    for name, child in self._children.items()
                )
            )
        return tuple(_sample_node(child) for child in self._children)

    def drive(self, value: Any) -> None:
        if self._kind == "struct":
            if (
                isinstance(self._layout, _PackedStructLayout)
                and self._layout.allow_overlap
            ):
                raise ValueError(
                    f"{self._path} has overlapping fields; drive an explicit "
                    "leaf instead of the whole view"
                )
            if not isinstance(value, Mapping):
                raise TypeError(f"{self._path} drive value must be a mapping")
            missing = tuple(name for name in self._children if name not in value)
            extra = tuple(name for name in value if name not in self._children)
            if missing or extra:
                raise ValueError(
                    f"{self._path} shape mismatch: missing={missing}, "
                    f"extra={extra}"
                )
            for name, child in self._children.items():
                _drive_node(child, value[name], f"{self._path}.{name}")
            return
        if isinstance(value, Mapping):
            expected = set(self._indices)
            missing = tuple(
                index for index in self._indices if index not in value
            )
            extra = tuple(index for index in value if index not in expected)
            if missing or extra:
                raise ValueError(
                    f"{self._path} shape mismatch: missing={missing}, "
                    f"extra={extra}"
                )
            ordered = tuple(value[index] for index in self._indices)
        elif _is_sequence(value) and len(value) == len(self._children):
            ordered = tuple(value)
        else:
            raise ValueError(
                f"{self._path} requires {len(self._children)} values"
            )
        for index, child, child_value in zip(
            self._indices, self._children, ordered
        ):
            _drive_node(child, child_value, f"{self._path}[{index}]")


class _SignalSlice:
    """Read-modify-write bit slice for signals without ``SubDataRef``.

    Real Picker/xcomm ``XData`` objects provide ``SubDataRef`` and never use
    this class. It exists for lightweight Python signal doubles (and other
    backends that expose only ``U``/``Set``) so a packed Layout can still be
    projected onto them. The adapter is intentionally not an ``XData``
    replacement: every write reads the parent, replaces the selected bits and
    writes the whole parent back.
    """

    __slots__ = ("_low", "_name", "_parent", "_width")

    def __init__(
        self,
        parent: Any,
        low: int,
        width: int,
        name: str,
    ) -> None:
        self._parent = parent
        self._low = low
        self._width = width
        self._name = name

    def W(self) -> int:
        return self._width

    @property
    def value(self) -> int:
        return self.U()

    @value.setter
    def value(self, value: Any) -> None:
        self.Set(value)

    def U(self) -> int:
        value = read_signal(self._parent)
        if isinstance(value, LogicValue):
            value = value.as_int(strict=False)
        return (int(value) >> self._low) & ((1 << self._width) - 1)

    def XMask(self) -> int:
        getter = getattr(self._parent, "XMask", None)
        if not callable(getter):
            value = read_signal(self._parent)
            if isinstance(value, LogicValue):
                return (
                    value.x_mask >> self._low
                ) & ((1 << self._width) - 1)
            return 0
        return (
            int(getter()) >> self._low
        ) & ((1 << self._width) - 1)

    def Set(self, value: Any) -> None:
        if isinstance(value, LogicValue):
            if not value.is_known:
                raise ValueError(
                    f"{self._name} contains X/Z; four-state drive is not "
                    "implemented"
                )
            value = value.value
        current = read_signal(self._parent)
        if isinstance(current, LogicValue):
            if not current.is_known:
                raise ValueError(
                    f"{self._name} cannot drive a slice while its parent "
                    "contains X/Z"
                )
            current = current.value
        mask = (1 << self._width) - 1
        shifted = mask << self._low
        combined = (
            int(current) & ~shifted
        ) | ((int(value) & mask) << self._low)
        write_signal(self._parent, combined, self._name)


class _SignalSlices:
    """Expose bit slices for signals without native sub-signal support."""

    __slots__ = ("_signal", "_width")

    def __init__(self, signal: Any) -> None:
        self._signal = signal
        self._width = signal_width(signal)

    def W(self) -> int:
        return self._width

    def SubDataRef(self, low: int, width: int, name: str) -> _SignalSlice:
        return _SignalSlice(self._signal, low, width, name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._signal, name)


class PackedArray(Sequence[Any]):
    """A live array view over equal-width slices of one packed XData bus."""

    __slots__ = ("_parent", "_items", "element_width", "lsb_first", "name")

    def __init__(
        self,
        signal: Any,
        element_width: int,
        count: int | None = None,
        *,
        lsb_first: bool = True,
        name: str = "packed",
    ) -> None:
        if element_width <= 0:
            raise ValueError("PackedArray element_width must be positive")
        parent = as_xdata(signal)
        total_width = signal_width(parent)
        if count is None:
            count, remainder = divmod(total_width, element_width)
            if remainder:
                raise ValueError(
                    f"packed width {total_width} is not divisible by "
                    f"element width {element_width}"
                )
        if count <= 0:
            raise ValueError("PackedArray count must be positive")
        if count * element_width != total_width:
            raise ValueError(
                f"PackedArray shape uses {count * element_width} bits, "
                f"but signal width is {total_width}"
            )
        sub_data_ref = getattr(parent, "SubDataRef", None)
        if not callable(sub_data_ref):
            raise TypeError("PackedArray signal must provide SubDataRef")
        items = []
        for index in range(count):
            physical = index if lsb_first else count - 1 - index
            items.append(
                sub_data_ref(
                    physical * element_width,
                    element_width,
                    f"{name}[{index}]",
                )
            )
        self._parent = parent
        self._items = tuple(items)
        self.element_width = element_width
        self.lsb_first = lsb_first
        self.name = name

    @property
    def parent(self) -> Any:
        return self._parent

    def __getitem__(self, index: int | slice) -> Any:
        return self._items[index]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


def split_packed(
    signal: Any,
    element_width: int,
    count: int | None = None,
    *,
    lsb_first: bool = True,
    name: str = "packed",
) -> PackedArray:
    return PackedArray(
        signal, element_width, count, lsb_first=lsb_first, name=name
    )


class SignalArray(Sequence[DataNode]):
    """Live view over an unpacked array, indexed by RTL indices.

    Items are stored, iterated and sampled in RTL declaration order
    (``left`` to ``right``). Integer keys are interpreted as RTL indices, so
    ``view[-1]`` on a declaration ``[-1:0]`` selects the element named
    ``[-1]``, not the last Python item. A mapping passed to :meth:`drive`
    uses the same RTL indices; a sequence is consumed in declaration order.
    Python slices remain positional slices of the declaration-order items.
    """

    __slots__ = ("_bounds", "_indices", "_items", "_path")

    def __init__(self, container: Any, *, path: str = "array") -> None:
        bounds = _array_bounds(container, path)
        indices = _array_indices(bounds[0])
        items = []
        for index in indices:
            item_path = f"{path}[{index}]"
            item = _array_element(container, index, item_path, bounds[0])
            items.append(_normalize_node(item, item_path))
        self._bounds = bounds
        self._indices = indices
        self._items = tuple(items)
        self._path = path

    @property
    def bounds(self) -> tuple[tuple[int, int], ...]:
        return self._bounds

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(
            abs(left - right) + 1 for left, right in self._bounds
        )

    @property
    def indices(self) -> tuple[int, ...]:
        return self._indices

    def __getitem__(self, key: int | slice) -> Any:
        if isinstance(key, slice):
            return self._items[key]
        position = _rtl_position(self._bounds[0], key, self._path)
        return self._items[position]

    def __iter__(self) -> Iterator[DataNode]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def sample(self) -> ValueNode:
        return tuple(_sample_node(item) for item in self._items)

    def drive(self, value: Any) -> None:
        if isinstance(value, Mapping):
            expected = set(self._indices)
            missing = tuple(index for index in self._indices if index not in value)
            extra = tuple(index for index in value if index not in expected)
            if missing or extra:
                raise ValueError(
                    f"{self._path} shape mismatch: missing={missing}, "
                    f"extra={extra}"
                )
            ordered = tuple(value[index] for index in self._indices)
        elif _is_sequence(value):
            if len(value) != len(self._items):
                raise ValueError(
                    f"{self._path} requires {len(self._items)} values"
                )
            ordered = tuple(value)
        else:
            raise TypeError(
                f"{self._path} drive value must be a mapping or sequence"
            )
        for index, item, item_value in zip(
            self._indices, self._items, ordered
        ):
            _drive_node(item, item_value, f"{self._path}[{index}]")

    def leaves(self) -> Iterator[tuple[str, Any]]:
        yield from _iter_leaves(self, self._path)


class PortView:
    """Bind one Picker port together with its parsed layout metadata.

    The underlying node is a signal, :class:`Bundle`, :class:`PackedView` or
    :class:`SignalArray`. Leaf identity, sampling and driving are delegated
    to that node; ``layout`` and ``unpacked`` expose the metadata that Picker
    emitted for the port.
    """

    __slots__ = ("_node", "_layout", "_unpacked", "_path")

    def __init__(
        self,
        node: DataNode,
        *,
        layout: PortLayout | None = None,
        unpacked: tuple[tuple[int, int], ...] = (),
        path: str = "port",
    ) -> None:
        if isinstance(node, PortView):
            if layout is None:
                layout = node.layout
            if not unpacked:
                unpacked = node.unpacked
            node = node.node
        if layout is not None and not isinstance(layout, PortLayout):
            raise TypeError("PortView layout must be a PortLayout")
        self._node = _normalize_node(node, path)
        self._layout = layout
        self._unpacked = tuple(unpacked)
        self._path = path

    @property
    def node(self) -> DataNode:
        return self._node

    @property
    def layout(self) -> PortLayout | None:
        return self._layout

    @property
    def unpacked(self) -> tuple[tuple[int, int], ...]:
        return self._unpacked

    @property
    def kind(self) -> str:
        if self._layout is not None:
            return self._layout.kind
        return _data_kind(self._node)

    @property
    def bounds(self) -> tuple[tuple[int, int], ...]:
        """Bounds of the live indexable node, not just the declared type.

        A collapsed packed array is one packed leaf even though its
        ``layout.kind == "array"``; it has no per-element views, so this
        property raises. Use ``layout.left``/``layout.right`` for declaration
        metadata.
        """
        node_bounds = getattr(self._node, "bounds", None)
        if node_bounds is not None:
            return tuple(node_bounds)
        if isinstance(self._node, tuple) and self._node:
            return ((0, len(self._node) - 1),)
        raise AttributeError(
            f"{self._path} is not an indexable array; use layout bounds"
        )

    @property
    def shape(self) -> tuple[int, ...]:
        node_shape = getattr(self._node, "shape", None)
        if node_shape is not None:
            return tuple(node_shape)
        return tuple(abs(left - right) + 1 for left, right in self.bounds)

    @property
    def indices(self) -> tuple[int, ...]:
        """RTL indices of live element views (``layout.indices`` is metadata)."""
        node_indices = getattr(self._node, "indices", None)
        if node_indices is not None:
            return tuple(node_indices)
        if isinstance(self._node, tuple):
            return tuple(range(len(self._node)))
        raise AttributeError(
            f"{self._path} is not an indexable array; use layout.indices"
        )

    @property
    def fields(self) -> Mapping[str, DataNode]:
        try:
            return self._node.fields
        except AttributeError as error:
            raise AttributeError(
                f"{self._path} has no named fields"
            ) from error

    def __getitem__(self, key: Any) -> DataNode:
        return self._node[key]  # type: ignore[index]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._node)

    def __len__(self) -> int:
        return len(self._node)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> Any:
        if name in {"bounds", "indices"}:
            # The property above already decided the live node is not
            # indexable; do not mask that with the node's own AttributeError.
            raise AttributeError(
                f"{self._path} is not an indexable array; "
                f"use layout.{name}"
            )
        if name == "fields":
            raise AttributeError(f"{self._path} has no named fields")
        return getattr(self._node, name)

    def leaves(self) -> Iterator[tuple[str, Any]]:
        yield from _iter_leaves(self._node, self._path)

    def sample(self) -> ValueNode:
        return _sample_node(self._node)

    def drive(self, value: Any) -> None:
        _drive_node(self._node, value, self._path)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _is_xview(value: Any) -> bool:
    kind = getattr(value, "Kind", None)
    if not callable(kind):
        return False
    return any(
        callable(getattr(value, name, None))
        for name in ("Array", "Data", "Struct")
    )


def _is_picker_array(value: Any) -> bool:
    if isinstance(
        value,
        (
            Bundle,
            Mapping,
            PackedArray,
            PackedView,
            PortView,
            SignalArray,
            bytes,
            bytearray,
            str,
        ),
    ):
        return False
    if getattr(value, "bounds", None) is not None and (
        callable(getattr(value, "__getitem__", None))
        or callable(getattr(value, "At", None))
    ):
        return True
    return getattr(value, "shape", None) is not None and callable(
        getattr(value, "At", None)
    )


def _is_picker_struct(value: Any) -> bool:
    if isinstance(
        value,
        (
            Bundle,
            Mapping,
            PackedArray,
            PackedView,
            PortView,
            SignalArray,
            bytes,
            bytearray,
            str,
        ),
    ):
        return False
    if callable(getattr(value, "Names", None)):
        return True
    if getattr(value, "fields", None) is None:
        return False
    return any(
        callable(getattr(value, name, None))
        for name in ("Array", "Data", "Struct")
    )


def _normalize_node(value: Any, path: str) -> DataNode:
    if isinstance(value, (PackedArray, PackedView, PortView, SignalArray)):
        return value
    if isinstance(value, Bundle):
        return value
    if _is_xview(value):
        return _normalize_xview(value, path)
    if _is_picker_array(value):
        return _normalize_xarray(value, path)
    if _is_picker_struct(value):
        return _normalize_xstruct(value, path)
    if isinstance(value, Mapping):
        return Bundle(value, path=path)
    if _is_sequence(value):
        if not value:
            raise ValueError(f"{path} sequence cannot be empty")
        return tuple(
            _normalize_node(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    packed_data = getattr(value, "PackedData", None)
    if callable(packed_data):
        data = packed_data()
        if data is not value:
            return _normalize_node(data, path)
    if not any(
        hasattr(value, name)
        for name in ("U", "W", "Set", "value", "xdata")
    ):
        raise TypeError(
            f"{path} must be a signal, Bundle, sequence, or Picker "
            f"XArray/XStruct/XView; got {type(value).__name__}"
        )
    return as_xdata(value)


def _normalize_xview(value: Any, path: str) -> DataNode:
    kind = str(value.Kind())
    if kind == "bits":
        data = value.Data()
        if data is None or data is value:
            raise ValueError(
                f"{path} unsupported aggregate metadata: XView bits node "
                "does not expose an underlying signal"
            )
        return _normalize_node(data, path)
    if kind == "array":
        child = value.Array()
        if child is None:
            raise ValueError(
                f"{path} unsupported aggregate metadata: XView array node "
                "does not expose an XArray"
            )
        if child is value:
            return _normalize_xarray(value, path)
        return _normalize_xarray(child, path)
    if kind == "struct":
        child = value.Struct()
        if child is None:
            raise ValueError(
                f"{path} unsupported aggregate metadata: XView struct "
                "node does not expose an XStruct"
            )
        if child is value:
            return _normalize_xstruct(value, path)
        return _normalize_xstruct(child, path)
    raise ValueError(
        f"{path} unsupported aggregate metadata: XView kind {kind!r}"
    )


def _normalize_xarray(value: Any, path: str) -> DataNode:
    if _is_picker_array(value):
        is_packed = getattr(value, "IsPacked", None)
        if callable(is_packed) and is_packed():
            packed_data = getattr(value, "PackedData", None)
            if callable(packed_data):
                try:
                    data = packed_data()
                except (RuntimeError, TypeError, ValueError):
                    data = None
                if data is not None and data is not value:
                    # A packed array is one packed value, not N independent
                    # one-bit leaves. Collapse it so callers see a real
                    # 5-/65-/N-bit signal instead of bit soup.
                    return _normalize_node(data, path)
        return SignalArray(value, path=path)
    packed_data = getattr(value, "PackedData", None)
    if callable(packed_data):
        data = packed_data()
        if data is not value:
            return _normalize_node(data, path)
    raise ValueError(
        f"{path} unsupported aggregate metadata: XArray has no indexed "
        "elements or packed data"
    )


def _safe_field_name(name: Any, path: str) -> str:
    if not isinstance(name, str):
        raise ValueError(f"{path} field name must be a string: {name!r}")
    field_name = f"{name}_" if iskeyword(name) else name
    if not field_name.isidentifier():
        raise ValueError(
            f"{path} field is not a Python identifier: {name!r}"
        )
    if field_name.startswith("_"):
        raise ValueError(f"{path} field cannot start with '_': {name!r}")
    return field_name


def _normalize_xstruct(value: Any, path: str) -> DataNode:
    names = _xstruct_names(value, path)
    fields: dict[str, DataNode] = {}
    for raw_name in names:
        field_name = _safe_field_name(raw_name, path)
        if field_name in fields:
            raise ValueError(
                f"{path} duplicate struct field after name mapping: "
                f"{raw_name!r}"
            )
        fields[field_name] = _normalize_node(
            _xstruct_member(value, raw_name, path),
            f"{path}.{field_name}",
        )
    return Bundle(fields, path=path)


def _xstruct_names(value: Any, path: str) -> tuple[str, ...]:
    names_getter = getattr(value, "Names", None)
    raw_names: Any
    if callable(names_getter):
        raw_names = names_getter()
    else:
        raw_fields = getattr(value, "fields", None)
        if callable(raw_fields):
            raw_fields = raw_fields()
        if isinstance(raw_fields, Mapping):
            raw_names = tuple(raw_fields)
        elif _is_sequence(raw_fields):
            raw_names = tuple(
                getattr(field, "name", field) for field in raw_fields
            )
        else:
            raw_names = ()
    names = tuple(str(name) for name in raw_names)
    if not names:
        raise ValueError(
            f"{path} unsupported aggregate metadata: XStruct has no fields"
        )
    return names


def _xstruct_member(value: Any, name: str, path: str) -> Any:
    raw_fields = getattr(value, "fields", None)
    if callable(raw_fields):
        raw_fields = raw_fields()
    if isinstance(raw_fields, Mapping):
        return raw_fields[name]
    field_getter = getattr(value, "Field", None)
    if callable(field_getter):
        field = field_getter(name)
        kind_getter = getattr(field, "Kind", None)
        if callable(kind_getter):
            kind = str(kind_getter())
            accessor = {
                "bits": "Data",
                "struct": "Struct",
                "array": "Array",
            }.get(kind)
            if accessor is not None:
                getter = getattr(field, accessor, None)
                if callable(getter):
                    return getter()
    # Probe struct before array before bits. ``Data`` would also succeed for
    # a packed struct or packed vector and lose its nested structure.
    for accessor in ("Struct", "Array", "Data"):
        getter = getattr(value, accessor, None)
        if not callable(getter):
            continue
        try:
            item = getter(name)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
        if item is not None:
            return item
    raise ValueError(
        f"{path} unsupported aggregate metadata: cannot select XStruct "
        f"field {name!r}"
    )


def _array_bounds(container: Any, path: str) -> tuple[tuple[int, int], ...]:
    raw_bounds = getattr(container, "bounds", None)
    if callable(raw_bounds):
        raw_bounds = raw_bounds()
    if raw_bounds is None:
        left_getter = getattr(container, "LeftBounds", None)
        right_getter = getattr(container, "RightBounds", None)
        if callable(left_getter) and callable(right_getter):
            raw_bounds = tuple(
                zip(left_getter(), right_getter())
            )
    if raw_bounds is None:
        raw_shape = getattr(container, "shape", None)
        if callable(raw_shape):
            raw_shape = raw_shape()
        if raw_shape is not None:
            if isinstance(raw_shape, int):
                raw_shape = (raw_shape,)
            raw_bounds = tuple(
                (0, int(count) - 1) for count in raw_shape
            )
    if (
        _is_sequence(raw_bounds)
        and len(raw_bounds) == 2
        and all(
            isinstance(bound, int) and not isinstance(bound, bool)
            for bound in raw_bounds
        )
    ):
        raw_bounds = (tuple(raw_bounds),)
    if not raw_bounds:
        raise ValueError(
            f"{path} unsupported aggregate metadata: XArray has no bounds"
        )
    bounds: list[tuple[int, int]] = []
    for entry in raw_bounds:
        if not _is_sequence(entry) or len(entry) != 2:
            raise ValueError(
                f"{path} unsupported aggregate metadata: invalid XArray "
                f"bounds {entry!r}"
            )
        bounds.append((int(entry[0]), int(entry[1])))
    return tuple(bounds)


def _array_indices(bounds: tuple[int, int]) -> tuple[int, ...]:
    left, right = bounds
    step = 1 if left <= right else -1
    return tuple(range(left, right + step, step))


def _rtl_position(bounds: tuple[int, int], index: Any, path: str) -> int:
    """Translate one RTL array index into a 0-based declaration-order slot."""
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError(f"{path} index must be an integer")
    left, right = bounds
    step = 1 if left <= right else -1
    position = (index - left) * step
    if not 0 <= position <= abs(left - right):
        raise IndexError(
            f"{path} RTL array index {index} outside [{left}:{right}]"
        )
    return position


def _array_element(
    container: Any,
    index: int,
    path: str,
    bounds: tuple[int, int] | None = None,
) -> Any:
    """Select one RTL-indexed element from an XArray or array-like object.

    Native containers expose ``At(rtl_index)`` and are authoritative. Plain
    Python sequences use positional ``container[position]``; when bounds are
    known the RTL index is translated first so descending declarations do
    not silently select the mirror element.
    """
    at_getter = getattr(container, "At", None)
    if callable(at_getter):
        item = at_getter(index)
        if item is not None:
            return item
    position = (
        _rtl_position(bounds, index, path) if bounds is not None else index
    )
    try:
        return container[position]
    except (IndexError, KeyError, TypeError):
        pass
    data_getter = getattr(container, "Data", None)
    if callable(data_getter):
        item = data_getter(index)
        if item is not None:
            return item
    raise ValueError(
        f"{path} unsupported aggregate metadata: cannot select XArray "
        f"element {index}"
    )



def _data_kind(value: DataNode) -> str:
    if isinstance(value, Bundle):
        return "struct"
    if isinstance(value, PackedView):
        return value.kind
    if isinstance(value, (PackedArray, SignalArray, tuple)):
        return "array"
    return "bits"


def _sample_node(value: DataNode) -> ValueNode:
    if isinstance(value, (Bundle, PackedView, PortView)):
        return value.sample()
    if isinstance(value, (tuple, PackedArray, SignalArray)):
        return tuple(_sample_node(item) for item in value)
    return sample_signal(value)


def _drive_node(target: DataNode, value: Any, path: str) -> None:
    if isinstance(target, (Bundle, PackedView, PortView)):
        target.drive(value)
        return
    if isinstance(target, SignalArray):
        target.drive(value)
        return
    if isinstance(target, (tuple, PackedArray)):
        if not _is_sequence(value) or len(value) != len(target):
            raise ValueError(f"{path} requires {len(target)} values")
        for index, (child, child_value) in enumerate(zip(target, value)):
            _drive_node(child, child_value, f"{path}[{index}]")
        return
    write_signal(target, value, path)


class Bundle(Mapping[str, DataNode]):
    """A deterministic, nested live view over bound signal leaves."""

    __slots__ = ("_fields", "_path")
    _reserved = frozenset(
        {"drive", "fields", "items", "keys", "leaves", "sample", "values", "compile_sampler"}
    )

    def __init__(
        self,
        fields: Mapping[str, Any] | None = None,
        /,
        *,
        path: str = "bundle",
        **named_fields: Any,
    ) -> None:
        if fields is not None and named_fields:
            raise TypeError("pass either a field mapping or keyword fields")
        source = fields if fields is not None else named_fields
        if not source:
            raise ValueError("Bundle requires at least one field")
        normalized: dict[str, DataNode] = {}
        for name, value in source.items():
            if (
                not isinstance(name, str)
                or not name.isidentifier()
                or iskeyword(name)
            ):
                raise ValueError(f"invalid Bundle field name: {name!r}")
            if name.startswith("_") or name in self._reserved:
                raise ValueError(f"reserved Bundle field name: {name}")
            normalized[name] = _normalize_node(value, f"{path}.{name}")
        object.__setattr__(self, "_fields", MappingProxyType(normalized))
        object.__setattr__(self, "_path", path)

    @property
    def fields(self) -> Mapping[str, DataNode]:
        return self._fields

    def __getitem__(self, name: str) -> DataNode:
        return self._fields[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._fields)

    def __len__(self) -> int:
        return len(self._fields)

    def __getattr__(self, name: str) -> DataNode:
        try:
            return self._fields[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def leaves(self) -> Iterator[tuple[str, Any]]:
        yield from _iter_leaves(self, self._path)

    def sample(self) -> "BundleValue":
        return BundleValue(
            tuple((name, _sample_node(value)) for name, value in self.items())
        )

    def compile_sampler(self):
        """Compile repeated sampling for this fixed tree and signal widths.

        Only structure and getters are cached. Every invocation reads fresh
        values and returns the same immutable BundleValue as sample(). Rebuild
        the sampler if leaves, getter implementations or widths are rebound.
        """
        return compile_sampler(self)

    def drive(self, value: Mapping[str, Any] | "BundleValue") -> None:
        if not isinstance(value, Mapping):
            raise TypeError("Bundle drive value must be a mapping")
        missing = tuple(name for name in self if name not in value)
        extra = tuple(name for name in value if name not in self)
        if missing or extra:
            raise ValueError(
                f"Bundle shape mismatch: missing={missing}, extra={extra}"
            )
        for name, target in self.items():
            _drive_node(target, value[name], f"{self._path}.{name}")

    @classmethod
    def bind(
        cls,
        root: Any,
        schema: Mapping[str, Any],
        *,
        path: str = "bundle",
    ) -> "Bundle":
        return cls(
            {
                name: _bind_node(root, spec, f"{path}.{name}")
                for name, spec in schema.items()
            },
            path=path,
        )

    @classmethod
    def bind_tree(
        cls,
        root: Any,
        tree: Mapping[str, Any],
        *,
        source_prefix: str = "",
        path: str = "bundle",
    ) -> "Bundle":
        """Bind Picker signal-tree metadata without inferring protocols."""
        node = _bind_tree_node(
            root,
            tree,
            (source_prefix,) if source_prefix else (),
            path,
        )
        if not isinstance(node, Bundle):
            raise ValueError("Bundle.bind_tree root must describe an aggregate")
        return node

    def view_as(
        self,
        schema: Mapping[str, Any],
        *,
        path: str | None = None,
    ) -> "Bundle":
        return Bundle.bind(self, schema, path=path or self._path)


@dataclass(frozen=True, slots=True)
class BundleValue(Mapping[str, ValueNode]):
    _items: tuple[tuple[str, ValueNode], ...]

    def __post_init__(self) -> None:
        names = tuple(name for name, _ in self._items)
        if len(set(names)) != len(names):
            raise ValueError("BundleValue fields must be unique")

    def __getitem__(self, name: str) -> ValueNode:
        for field, value in self._items:
            if field == name:
                return value
        raise KeyError(name)

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getattr__(self, name: str) -> ValueNode:
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def as_dict(self) -> dict[str, Any]:
        return {
            name: _value_as_plain(value) for name, value in self._items
        }


def _iter_leaves(node: DataNode, path: str) -> Iterator[tuple[str, Any]]:
    if isinstance(node, PortView):
        yield from _iter_leaves(node.node, path)
    elif isinstance(node, Bundle):
        for name, value in node.items():
            yield from _iter_leaves(value, f"{path}.{name}")
    elif isinstance(node, PackedView):
        if node.kind == "struct":
            for name, value in node.fields.items():
                yield from _iter_leaves(value, f"{path}.{name}")
        else:
            for index, value in zip(node.indices, node):
                yield from _iter_leaves(value, f"{path}[{index}]")
    elif isinstance(node, SignalArray):
        for index, value in zip(node.indices, node):
            yield from _iter_leaves(value, f"{path}[{index}]")
    elif isinstance(node, (tuple, PackedArray)):
        for index, value in enumerate(node):
            yield from _iter_leaves(value, f"{path}[{index}]")
    else:
        yield path, node


def _bind_node(root: Any, spec: Any, path: str) -> Any:
    if isinstance(spec, Field):
        signal = _resolve_source(root, spec.source)
        if spec.width is not None:
            actual = signal_width(signal)
            if actual != spec.width:
                raise ValueError(
                    f"{path} width mismatch: expected={spec.width}, "
                    f"actual={actual}"
                )
        return signal
    if isinstance(spec, Mapping):
        return {
            name: _bind_node(root, child, f"{path}.{name}")
            for name, child in spec.items()
        }
    if _is_sequence(spec):
        return tuple(
            _bind_node(root, child, f"{path}[{index}]")
            for index, child in enumerate(spec)
        )
    return _resolve_source(root, spec)


def _resolve_source(root: Any, source: Any) -> Any:
    if not isinstance(source, (str, tuple)):
        return source
    parts = source.split(".") if isinstance(source, str) else source
    value = root
    for part in parts:
        value = value[part] if isinstance(part, int) else getattr(value, part)
    return value


_TREE_METADATA = frozenset(
    {"_", "Pin", "High", "Low", "Unpacked", "Layout"}
)


def _bind_tree_node(
    root: Any,
    node: Mapping[str, Any],
    source_parts: tuple[str, ...],
    path: str,
) -> DataNode:
    if not isinstance(node, Mapping):
        raise TypeError(f"{path} signal-tree node must be a mapping")
    children = tuple(
        (name, child)
        for name, child in node.items()
        if name not in _TREE_METADATA
    )
    if node.get("_") is True:
        if children:
            raise ValueError(f"{path} cannot be both a leaf and aggregate")
        if not source_parts:
            raise ValueError(f"{path} leaf has no source path")
        return _bind_tree_leaf(root, node, source_parts, path)
    if not children:
        raise ValueError(f"{path} aggregate has no fields")

    if all(str(name).isdigit() for name, _ in children):
        indexed = sorted((int(name), child) for name, child in children)
        if [index for index, _ in indexed] != list(range(len(indexed))):
            raise ValueError(f"{path} sequence indices must be contiguous")
        return tuple(
            _bind_tree_node(
                root,
                child,
                source_parts + (str(index),),
                f"{path}[{index}]",
            )
            for index, child in indexed
        )

    fields: dict[str, DataNode] = {}
    for raw_name, child in children:
        field_name = f"{raw_name}_" if iskeyword(raw_name) else raw_name
        if not field_name.isidentifier():
            raise ValueError(
                f"{path} signal-tree field is not a Python identifier: "
                f"{raw_name!r}"
            )
        if field_name in fields:
            raise ValueError(f"{path} duplicate field after name mapping")
        fields[field_name] = _bind_tree_node(
            root,
            child,
            source_parts + (raw_name,),
            f"{path}.{field_name}",
        )
    return Bundle(fields, path=path)


def _bind_tree_leaf(
    root: Any,
    node: Mapping[str, Any],
    source_parts: tuple[str, ...],
    path: str,
) -> DataNode:
    source_name = "_".join(source_parts)
    try:
        signal = getattr(root, source_name)
    except AttributeError as error:
        raise ValueError(
            f"{path} signal-tree leaf {source_name!r} is not available on root"
        ) from error
    layout = _parse_port_layout(node.get("Layout"), path)
    unpacked = _parse_unpacked(node.get("Unpacked"), path)
    high = int(node["High"]) if "High" in node else None
    low = int(node.get("Low", 0)) if high is not None else None
    return _bind_port(signal, layout, unpacked, high, low, path)


def _parse_port_layout(raw: Any, path: str) -> PortLayout | None:
    if raw is None:
        return None
    try:
        return PortLayout.from_json(raw)
    except (TypeError, ValueError) as error:
        if "unsupported aggregate metadata" in str(error):
            raise ValueError(f"{path}: {error}") from error
        raise ValueError(
            f"{path} unsupported aggregate metadata: {error}"
        ) from error


def _parse_unpacked(
    raw: Any, path: str
) -> tuple[tuple[int, int], ...]:
    if raw is None or raw == []:
        return ()
    if not _is_sequence(raw):
        raise ValueError(
            f"{path} unsupported aggregate metadata: Unpacked must be a "
            "sequence of [left, right] pairs"
        )
    bounds: list[tuple[int, int]] = []
    for entry in raw:
        if not _is_sequence(entry) or len(entry) != 2:
            raise ValueError(
                f"{path} unsupported aggregate metadata: Unpacked entry "
                f"{entry!r} must be [left, right]"
            )
        try:
            left, right = int(entry[0]), int(entry[1])
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{path} unsupported aggregate metadata: Unpacked bounds "
                f"must be integers: {entry!r}"
            ) from error
        bounds.append((left, right))
    return tuple(bounds)


def _bind_port(
    signal: Any,
    layout: PortLayout | None,
    unpacked: tuple[tuple[int, int], ...],
    high: int | None,
    low: int | None,
    path: str,
) -> DataNode:
    if layout is None and not unpacked:
        _validate_tree_width(signal, high, low, path)
        return as_xdata(signal)
    if layout is not None and layout.kind == "bits" and not unpacked:
        _validate_tree_width(signal, high, low, path)
        return PortView(
            as_xdata(signal),
            layout=layout,
            unpacked=unpacked,
            path=path,
        )

    layout = _layout_with_unpacked(layout, unpacked, high, low)
    if layout is not None and layout.packed and not unpacked:
        packed_signal = _packed_port_data(signal, path)
        actual = signal_width(packed_signal)
        if actual != layout.width:
            raise ValueError(
                f"{path} unsupported aggregate metadata: packed layout width "
                f"{layout.width} does not match signal width {actual}"
            )
        packed_layout = _to_packed_layout(layout, path)
        if isinstance(packed_layout, _PackedBits):
            # A packed vector/matrix is one packed value. There is nothing to
            # slice, so keep the real signal identity instead of wrapping it.
            return PortView(
                packed_signal,
                layout=layout,
                unpacked=unpacked,
                path=path,
            )
        if not callable(getattr(packed_signal, "SubDataRef", None)):
            packed_signal = _SignalSlices(packed_signal)
        view = PackedView(packed_signal, packed_layout, name=path)
        return PortView(
            view,
            layout=layout,
            unpacked=unpacked,
            path=path,
        )
    if (
        _is_xview(signal)
        or _is_picker_array(signal)
        or _is_picker_struct(signal)
    ):
        return PortView(
            _normalize_node(signal, path),
            layout=layout,
            unpacked=unpacked,
            path=path,
        )
    if isinstance(signal, Mapping):
        return PortView(
            _normalize_node(signal, path),
            layout=layout,
            unpacked=unpacked,
            path=path,
        )
    if (
        layout is not None
        and layout.kind == "array"
        and _is_sequence(signal)
    ):
        return PortView(
            _normalize_node(signal, path),
            layout=layout,
            unpacked=unpacked,
            path=path,
        )
    if unpacked:
        raise ValueError(
            f"{path} unsupported aggregate metadata: unpacked dims "
            f"{unpacked} require an XArray/XStruct/sequence container, "
            f"got {type(signal).__name__}"
        )
    raise ValueError(
        f"{path} unsupported aggregate metadata: unpacked "
        f"{layout.kind if layout is not None else 'bits'} layout "
        "requires an XStruct/XArray container"
    )


def _validate_tree_width(
    signal: Any,
    high: int | None,
    low: int | None,
    path: str,
) -> None:
    if high is None:
        return
    assert low is not None
    expected = 1 if high < 0 else high - low + 1
    actual = signal_width(signal)
    if actual != expected:
        raise ValueError(
            f"{path} width mismatch: metadata={expected}, actual={actual}"
        )


def _layout_with_unpacked(
    layout: PortLayout | None,
    unpacked: tuple[tuple[int, int], ...],
    high: int | None,
    low: int | None,
) -> PortLayout | None:
    if not unpacked:
        return layout
    if layout is not None and layout.kind == "array":
        return layout
    element_width = 1 if high is None or high < 0 else high - low + 1
    base = layout
    if base is None:
        base = PortLayout(kind="bits", packed=True, width=element_width)
    for left, right in reversed(unpacked):
        base = PortLayout(
            kind="array",
            packed=False,
            width=0,
            left=left,
            right=right,
            element=base,
        )
    return base


def _packed_port_data(signal: Any, path: str) -> Any:
    packed_data = getattr(signal, "PackedData", None)
    packed_error: BaseException | None = None
    if callable(packed_data):
        try:
            data = packed_data()
        except (RuntimeError, TypeError, ValueError) as error:
            packed_error = error
        else:
            if data is not None:
                return as_xdata(data)
    if not any(
        hasattr(signal, name)
        for name in ("U", "W", "Set", "value", "xdata")
    ):
        detail = (
            f" (PackedData failed: {packed_error})"
            if packed_error is not None
            else ""
        )
        raise ValueError(
            f"{path} unsupported aggregate metadata: packed layout "
            f"requires an XData signal, got {type(signal).__name__}{detail}"
        )
    return as_xdata(signal)


def _packed_has_struct(layout: PortLayout) -> bool:
    if layout.kind == "struct":
        return True
    if layout.kind == "array" and layout.element is not None:
        return _packed_has_struct(layout.element)
    return False


def _to_packed_layout(
    layout: PortLayout,
    path: str,
    *,
    offset: int | None = None,
) -> PackedLayout:
    """Project Picker's packed Layout onto :class:`PackedLayout`.

    Picker describes every packed vector dimension as an ``array`` node whose
    ``width`` is already the expanded width of the dimension, down to a
    ``bits`` node with ``width == 0`` for a one-bit scalar. A packed vector
    or a packed array of vectors is therefore *one* packed leaf: recursing
    into it would turn ``logic [0:4] tag`` into five one-bit leaves and
    ``[1:0][-2:1] matrix`` into a 2x4 grid of one-bit leaves, corrupting both
    integer driving and bit order.

    A packed array whose element (recursively) contains a struct keeps
    per-element views so fields remain reachable. Those arrays are
    MSB-first: Picker's ``ViewNode::Child()`` places the left RTL index at
    ``(size - position - 1) * element_bits``.
    """
    if not layout.packed:
        raise ValueError(
            f"{path} unsupported aggregate metadata: unpacked "
            f"{layout.kind} layout cannot become a packed view"
        )
    if layout.kind == "bits":
        return PackedLayout.bits(layout.width, offset=offset)
    if layout.kind == "array":
        assert layout.element is not None
        if not _packed_has_struct(layout.element):
            return PackedLayout.bits(layout.width, offset=offset)
        element = _to_packed_layout(layout.element, f"{path}[]")
        return PackedLayout.array(
            layout.count,
            element,
            lsb_first=False,
            offset=offset,
            left=layout.left,
            right=layout.right,
        )
    fields: dict[str, PackedLayout] = {}
    for field in layout.fields:
        field_name = _packed_field_name(field.name, path)
        if field_name in fields:
            raise ValueError(
                f"{path} unsupported aggregate metadata: duplicate packed "
                f"field name {field.name!r}"
            )
        fields[field_name] = _to_packed_layout(
            field.type,
            f"{path}.{field_name}",
            offset=field.offset,
        )
    try:
        return PackedLayout.struct(
            fields,
            width=layout.width,
            offset=offset,
        )
    except ValueError as error:
        raise ValueError(
            f"{path} unsupported aggregate metadata: {error}"
        ) from error


def _packed_field_name(name: str, path: str) -> str:
    field_name = f"{name}_" if iskeyword(name) else name
    if field_name in PackedLayout._reserved_fields or field_name in {
        "bounds",
        "indices",
        "node",
        "path",
        "shape",
        "unpacked",
    }:
        field_name += "_"
    if not field_name.isidentifier() or field_name.startswith("_"):
        raise ValueError(
            f"{path} unsupported aggregate metadata: packed field name "
            f"{name!r} is not a valid Python identifier"
        )
    return field_name


def iter_data_leaves(
    node: DataNode, path: str = "data"
) -> Iterator[tuple[str, Any]]:
    yield from _iter_leaves(node, path)


def normalize_data(node: DataNode, path: str = "data") -> DataNode:
    return _normalize_node(node, path)


def sample_data(node: DataNode) -> ValueNode:
    return _sample_node(node)


def compile_sampler(node: DataNode):
    """Compile fresh sampling of a fixed DataNode tree and declared widths.

    Packed views retain their normal wide/four-state path. Leaves without
    explicit widths retain dynamic width inference. Recompile after rebinding.
    """
    if isinstance(node, Bundle):
        fields = tuple((name, compile_sampler(value)) for name, value in node.items())
        return lambda: BundleValue(tuple((name, sample()) for name, sample in fields))
    if isinstance(node, (PackedView, PackedArray, PortView, SignalArray)):
        return lambda: _sample_node(node)
    if isinstance(node, tuple):
        samplers = tuple(compile_sampler(value) for value in node)
        return lambda: tuple(sample() for sample in samplers)
    return compile_signal_sampler(node)


def drive_data(node: DataNode, value: Any, path: str = "data") -> None:
    _drive_node(node, value, path)


def _value_as_plain(value: ValueNode) -> Any:
    if isinstance(value, LogicValue):
        return value.as_int()
    if isinstance(value, BundleValue):
        return value.as_dict()
    return tuple(_value_as_plain(item) for item in value)
