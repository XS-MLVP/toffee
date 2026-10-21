from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from keyword import iskeyword
from types import MappingProxyType
from typing import Any

from .events import LogicValue
from .signals import as_xdata, sample_signal, signal_width, write_signal
from .signals import compile_signal_sampler

DataNode = Any
ValueNode = Any


@dataclass(frozen=True, slots=True)
class Field:
    source: Any
    width: int | None = None

    def __post_init__(self) -> None:
        if self.width is not None and self.width <= 0:
            raise ValueError("Field width must be positive")


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

    @property
    def width(self) -> int:
        return (self.count - 1) * self.stride + self.element.width


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
    """Live recursive view whose leaves directly slice one root XData."""

    __slots__ = ("_parent", "_layout", "_kind", "_children", "_path")

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
            items = []
            for index in range(layout.count):
                physical = (
                    index if layout.lsb_first else layout.count - 1 - index
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
            self._children = tuple(items)
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
    def fields(self) -> Mapping[str, Any]:
        if self._kind != "struct":
            raise AttributeError("array PackedView has no fields")
        return self._children

    def __getitem__(self, key: int | slice | str) -> Any:
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
        if not _is_sequence(value) or len(value) != len(self._children):
            raise ValueError(
                f"{self._path} requires {len(self._children)} values"
            )
        for index, (child, child_value) in enumerate(
            zip(self._children, value)
        ):
            _drive_node(child, child_value, f"{self._path}[{index}]")


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


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _normalize_node(value: Any, path: str) -> DataNode:
    if isinstance(value, (PackedArray, PackedView)):
        return value
    if isinstance(value, Bundle):
        return value
    if isinstance(value, Mapping):
        return Bundle(value, path=path)
    if _is_sequence(value):
        if not value:
            raise ValueError(f"{path} sequence cannot be empty")
        return tuple(
            _normalize_node(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if not any(
        hasattr(value, name)
        for name in ("U", "W", "Set", "value", "xdata")
    ):
        raise TypeError(f"{path} must be a signal, Bundle, or sequence")
    return as_xdata(value)


def _sample_node(value: DataNode) -> ValueNode:
    if isinstance(value, (Bundle, PackedView)):
        return value.sample()
    if isinstance(value, (tuple, PackedArray)):
        return tuple(_sample_node(item) for item in value)
    return sample_signal(value)


def _drive_node(target: DataNode, value: Any, path: str) -> None:
    if isinstance(target, (Bundle, PackedView)):
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
    if isinstance(node, Bundle):
        for name, value in node.items():
            yield from _iter_leaves(value, f"{path}.{name}")
    elif isinstance(node, PackedView):
        if node.kind == "struct":
            for name, value in node.fields.items():
                yield from _iter_leaves(value, f"{path}.{name}")
        else:
            for index, value in enumerate(node):
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


_TREE_METADATA = frozenset({"_", "Pin", "High", "Low"})


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
        signal = as_xdata(getattr(root, "_".join(source_parts)))
        if "High" in node:
            high = int(node["High"])
            low = int(node.get("Low", 0))
            expected = 1 if high < 0 else high - low + 1
            actual = signal_width(signal)
            if actual != expected:
                raise ValueError(
                    f"{path} width mismatch: metadata={expected}, "
                    f"actual={actual}"
                )
        return signal
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
    if isinstance(node, (PackedView, PackedArray)):
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
