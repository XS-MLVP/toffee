"""Validated class collection, existing-schema lowering and review artifacts."""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
import inspect
import json
from types import MappingProxyType
from typing import Generic, TypeVar, get_args, get_origin, get_type_hints
from weakref import WeakKeyDictionary

from ..decorators import TriggerDefinition, PythonTriggerDefinition
from ..ir import program_signal_paths, PythonPredicateExpr
from ..data import Bundle

from ..coverage import CoverGroupDef, CoverPointDef, CrossDef, Iff as CoreIff, OverlapPolicy
from ._declarations import (
    Bin, BinRule, BinSelection, CoverPoint, TemporalCoverPoint, CoverageFragment, CoverageReferenceError, Cross,
    DefinitionError, FieldRef, Gate, scalar_type,
)

S = TypeVar("S")
_CACHE = WeakKeyDictionary()
_BIN_OWNERS = WeakKeyDictionary()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _type_id(cls):
    return f"{cls.__module__}.{cls.__qualname__}"


def _origin(cls):
    try:
        return f"{inspect.getsourcefile(cls)}:{inspect.getsourcelines(cls)[1]} ({_type_id(cls)})"
    except (TypeError, OSError):
        return _type_id(cls)


def _fail(model, path, code, message):
    raise DefinitionError(code, f"{model.__qualname__}.{path} [{_origin(model)}]", message)


def _generic_type(cls, markers, model, path):
    found = set()
    for base in cls.__mro__:
        for generic in vars(base).get("__orig_bases__", ()):
            if get_origin(generic) in markers:
                found.update(arg for arg in get_args(generic) if isinstance(arg, type))
    if len(found) != 1 or not isinstance(next(iter(found)), type):
        _fail(model, path, "E_TYPE", "provide one concrete and invariant generic type")
    return next(iter(found))


def _sample_shape(sample_type, model, path="sample", stack=()):
    if sample_type in stack or not is_dataclass(sample_type) or not sample_type.__dataclass_params__.frozen:
        _fail(model, path, "E_SAMPLE_TYPE", "use a non-recursive frozen dataclass")
    hints = get_type_hints(sample_type)
    result = {}
    for field in fields(sample_type):
        value_type = hints.get(field.name)
        if scalar_type(value_type):
            leaf = {"type": _type_id(value_type)}
            if hasattr(value_type, "__members__"):
                leaf["members"] = {name: value.value for name, value in value_type.__members__.items()}
                if any(type(value) not in (int, bool, str) for value in leaf["members"].values()):
                    _fail(model, f"{path}.{field.name}", "E_TYPE", "Enum values must be supported scalars")
            result[field.name] = leaf
        elif isinstance(value_type, type) and is_dataclass(value_type):
            result[field.name] = _sample_shape(value_type, model, f"{path}.{field.name}", (*stack, sample_type))
        else:
            _fail(model, f"{path}.{field.name}", "E_TYPE", "unsupported sample field type")
    return {"type": _type_id(sample_type), "fields": result}


def _field_type(sample_type, path, model, slot):
    current = sample_type
    for part in path:
        if not (is_dataclass(current) or isinstance(current, type) and issubclass(current, Bundle)) or part not in get_type_hints(current):
            _fail(model, slot, "E_FIELD", f"field {'.'.join(path)!r} does not exist on {_type_id(sample_type)}")
        current = get_type_hints(current)[part]
    return current


def _checked_field(ref, sample_type, model, slot):
    if not isinstance(ref, FieldRef) or ref.root is not sample_type:
        _fail(model, slot, "E_FIELD_ROOT", "field reference belongs to another sample type")
    actual = _field_type(sample_type, ref.path, model, slot)
    live = isinstance(sample_type, type) and issubclass(sample_type, Bundle)
    if (live and ref.value_type not in (int, bool)) or (not live and (actual is not ref.value_type or not scalar_type(actual))):
        _fail(model, slot, "E_FIELD_TYPE", "field reference must describe a supported scalar leaf")
    return ref


def _signature(model):
    classes = list(model.__mro__)
    def add_sample(sample):
        if isinstance(sample, type) and is_dataclass(sample) and sample not in classes:
            classes.append(sample)
            for hint in get_type_hints(sample).values():
                add_sample(hint)
    for base in model.__mro__:
        for generic in vars(base).get("__orig_bases__", ()):
            for argument in get_args(generic):
                add_sample(argument)
        for value in vars(base).values():
            if isinstance(value, CoverPoint):
                classes.extend(type(value).__mro__)
                if value.source is not None:
                    add_sample(value.source.root)
    def stamp(value):
        if isinstance(value, CoverPoint):
            return (id(value), repr(value), tuple((id(owner), name) for owner, name in value._owners))
        if isinstance(value, (BinRule, Cross)):
            return (id(value), repr(value))
        return repr(value)
    return tuple((id(cls), tuple(sorted((key, stamp(value)) for key, value in vars(cls).items()
                                       if not key.startswith("__"))),
                  repr(vars(cls).get("__annotations__")), repr(vars(cls).get("__orig_bases__")),
                  repr(vars(cls).get("__dataclass_fields__")), repr(vars(cls).get("__dataclass_params__")))
                 for cls in dict.fromkeys(classes))


def _validation_plan(sample_type):
    if scalar_type(sample_type):
        return (sample_type, ())
    hints = get_type_hints(sample_type)
    return (sample_type, tuple((field.name, _validation_plan(hints[field.name])) for field in fields(sample_type)))


@dataclass(frozen=True)
class CompiledGroup(Generic[S]):
    schema: CoverGroupDef
    sample_type: type[S]
    contract: str
    input_digest: str
    _input_json: str
    _review_json: str
    _aliases: object
    _cross_aliases: object
    _rules: object
    _fields: tuple[FieldRef, ...]
    _validation: object

    @property
    def schema_digest(self) -> str:
        return self.schema.digest

    @property
    def sampling_contract(self) -> str:
        return f"{self.contract}|declarative-v2-experiment|input={self.input_digest}"

    def schema_json(self) -> str:
        return json.dumps(self.schema.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    def input_contract_json(self) -> str:
        return self._input_json

    def review_dict(self) -> dict:
        return json.loads(self._review_json)

    def resolve_point(self, point) -> str:
        try:
            return self._aliases[point]
        except KeyError as error:
            raise CoverageReferenceError("point belongs to another group") from error

    def resolve_cross(self, cross) -> str:
        try:
            return self._cross_aliases[cross]
        except KeyError as error:
            raise CoverageReferenceError("cross belongs to another group") from error

    def resolve_bin(self, point_name, rule) -> str:
        try:
            return self._rules[point_name][rule]
        except KeyError as error:
            raise CoverageReferenceError(f"bin does not belong to point {point_name!r}") from error

    def explain(self) -> str:
        view = self.review_dict()
        lines = [f"group {view['schema_id']}", f"  sample: {view['sample_type']}"]
        for point in view["points"]:
            source = ".".join(point["field"]) if point["field"] else "live programs"
            lines.append(f"  point {point['name']}: {point['value_type']} <- {source}")
            lines.append(f"    declared at {point['origin']}")
            lines.append(f"    goal={point['goal']} weight={point['weight']} iff={point['iff']}")
            for index, execution in enumerate(point.get("executions", [])):
                lines.append(f"    execution {index} ({execution['definition']}): {', '.join(execution['bins'])}")
            for bin in point["bins"]:
                lines.append(f"    {bin['name']}: {bin['kind']} {bin['matcher']} at_least={bin['at_least']}")
                lines.append(f"      from {bin['origin']}")
        for cross in view["crosses"]:
            lines.append(f"  cross {cross['name']}: {' x '.join(cross['points'])} ({cross['eligible_bins']} eligible bins)")
            lines.append(f"    declared at {cross['origin']}")
        lines.append(f"  group iff: {view['iff']}")
        return "\n".join(lines)

    def diff(self, other: CompiledGroup) -> list[dict]:
        """Compare actual schema/input semantics; exclude source locations and class labels."""
        result = []

        def walk(before, after, path):
            if isinstance(before, dict) and isinstance(after, dict):
                for key in sorted(before.keys() | after.keys()):
                    if key not in before:
                        result.append({"path": f"{path}.{key}", "change": "added", "after": after[key]})
                    elif key not in after:
                        result.append({"path": f"{path}.{key}", "change": "removed", "before": before[key]})
                    else:
                        walk(before[key], after[key], f"{path}.{key}")
            elif before != after:
                result.append({"path": path, "change": "changed", "before": before, "after": after})

        def normalized(compiled):
            schema = compiled.schema.to_dict()
            for point in schema["points"]:
                point["bins"] = {bin["name"]: bin for bin in point["bins"]}
            schema["points"] = {point["name"]: point for point in schema["points"]}
            schema["crosses"] = {cross["name"]: cross for cross in schema["crosses"]}
            schema["eligible_cross_bins"] = {cross.definition.name: [list(b) for b in cross.eligible]
                                                for cross in compiled.schema._resolved_crosses}
            return {"schema": schema, "input": json.loads(compiled._input_json), "contract": compiled.contract}

        walk(normalized(self), normalized(other), "model")
        return result


def compile_group(model):
    from ._runtime import CoverGroup, SignalCoverGroup
    live = issubclass(model, SignalCoverGroup)
    signature = _signature(model)
    if model in _CACHE:
        old_signature, compiled = _CACHE[model]
        if old_signature != signature:
            _fail(model, "declaration", "E_FROZEN", "compiled declarations changed; define a new subclass")
        return compiled
    sample_type = _generic_type(model, (CoverGroup, SignalCoverGroup, CoverageFragment), model, "sample")
    shape = {"type": _type_id(sample_type), "kind": "live"} if live else _sample_shape(sample_type, model)
    members, origins, aliases, cross_aliases, options = {}, {}, {}, {}, {}
    reserved = set(dir(CoverGroup)) | {"runtime", "snapshot"}
    declarations = [base for base in reversed(model.__mro__)
                    if base not in (CoverGroup, CoverageFragment)
                    and issubclass(base, (CoverGroup, CoverageFragment))]
    for base in declarations:
        concrete = [parent for parent in base.__bases__ if parent is not CoverGroup and issubclass(parent, CoverGroup)]
        if len(concrete) > 1:
            _fail(model, "inheritance", "E_INHERITANCE", "use one concrete Group lineage and explicit fragments")
        options.update(vars(base).get("_coverage_group_options", {}))
        for name, value in vars(base).items():
            if name.startswith("_"):
                continue
            previous = members.get(name)
            if name in reserved and isinstance(value, (CoverPoint, Cross)):
                _fail(model, name, "E_RESERVED", "declaration shadows a runtime method or property")
            if isinstance(previous, (CoverPoint, Cross)):
                if not isinstance(value, type(previous)):
                    # Point subclasses are handled by their specialized type below.
                    if not (isinstance(previous, CoverPoint) and isinstance(value, CoverPoint)
                            and issubclass(type(value), type(previous))):
                        _fail(model, name, "E_OVERRIDE", "override must specialize the original declaration")
                old_owner = origins[name]
                if not issubclass(base, old_owner) and name not in vars(model):
                    _fail(model, name, "E_CONFLICT", "independent fragments declare the same slot; resolve it explicitly")
            if isinstance(value, (CoverPoint, Cross)):
                members[name], origins[name] = value, base
            elif isinstance(previous, (CoverPoint, Cross)):
                _fail(model, name, "E_OVERRIDE", "cannot remove a declaration with a non-declaration")
            if isinstance(value, CoverPoint):
                if len(value._owners) != 1:
                    _fail(model, name, "E_REUSED_POINT", "construct a separate point for each declaration slot")
                aliases[value] = name
            elif isinstance(value, Cross):
                if value in cross_aliases and cross_aliases[value] != name:
                    _fail(model, name, "E_REUSED_CROSS", "construct a separate Cross for each slot")
                cross_aliases[value] = name
    point_defs, cross_defs, reviews, rules, used_fields = [], [], [], {}, {}
    pattern_definitions = {}

    def gate(gate_value, slot):
        if gate_value is None:
            return None
        if not isinstance(gate_value, Gate):
            _fail(model, slot, "E_GATE", "use Iff field comparisons")
        field = _checked_field(gate_value.field, sample_type, model, slot)
        used_fields[field.path] = field
        return CoreIff(".".join(field.path), gate_value.values, gate_value.negate)

    for name, point in sorted(members.items()):
        if not isinstance(point, CoverPoint):
            continue
        temporal = isinstance(point, TemporalCoverPoint)
        value_type = _generic_type(type(point), (CoverPoint, TemporalCoverPoint), model, name)
        if temporal and not live:
            _fail(model, name, "E_POINT_TYPE", "snapshot groups require value points; use SignalCoverGroup for pattern points")
        if temporal:
            if value_type is not sample_type or point.source is not None:
                _fail(model, name, "E_FIELD_ROOT", "pattern point must use this live root without a value source")
            field = None
        else:
            if not scalar_type(value_type):
                _fail(model, name, "E_POINT_TYPE", "point must have a supported scalar type")
            field = point.source or FieldRef(sample_type, (name,), value_type)
            field = _checked_field(field, sample_type, model, name)
            if field.value_type is not value_type:
                _fail(model, name, "E_FIELD_TYPE", "point type differs from bound field type")
            used_fields[field.path] = field
        bins, bin_origins, bin_aliases, point_options = {}, {}, {}, {}
        for base in reversed(type(point).__mro__):
            point_options.update(vars(base).get("_coverage_point_options", {}))
            for bin_name, rule in vars(base).items():
                reference = rule
                if isinstance(rule, (TriggerDefinition, PythonTriggerDefinition)):
                    if not temporal:
                        _fail(model, f"{name}.{bin_name}", "E_BIN_TYPE", "xtrigger bins require TemporalCoverPoint")
                    rule = Bin.pattern(rule)
                if bin_name in bins and not isinstance(rule, BinRule):
                    _fail(model, f"{name}.{bin_name}", "E_OVERRIDE", "cannot remove an inherited bin")
                if not isinstance(rule, BinRule):
                    continue
                if bin_name in set(dir(CoverPoint)) | {"source", "iff", "weight", "goal", "overlap"}:
                    _fail(model, f"{name}.{bin_name}", "E_RESERVED", "bin shadows the point API")
                if rule.value_type is not value_type:
                    _fail(model, f"{name}.{bin_name}", "E_BIN_TYPE", "bin type differs from point type")
                if temporal:
                    if rule._pattern is None:
                        _fail(model, f"{name}.{bin_name}", "E_BIN_TYPE", "pattern point requires xtrigger bins")
                    if reference in bin_aliases and bin_aliases[reference] != bin_name:
                        _fail(model, f"{name}.{bin_name}", "E_REUSED_BIN", "ambiguous repeated definition; use separate Bin.pattern configurations")
                    if isinstance(rule.spec.matcher.program, PythonPredicateExpr) and not options.get("contract"):
                        _fail(model, f"{name}.{bin_name}", "E_CONTRACT", "pytrigger bins require an explicit versioned observer contract")
                    for path in program_signal_paths(rule.spec.matcher.program):
                        used_fields[path] = FieldRef(sample_type, path, int)
                    pattern_definitions[(name, bin_name)] = rule._pattern
                    bins[bin_name], bin_origins[bin_name], bin_aliases[reference] = rule.spec, base, bin_name
                    continue
                owner = _BIN_OWNERS.get(rule)
                if owner is not None and owner != (base, bin_name):
                    _fail(model, f"{name}.{bin_name}", "E_REUSED_BIN", "bin object already declares another bin")
                _BIN_OWNERS[rule] = (base, bin_name)
                bins[bin_name], bin_origins[bin_name], bin_aliases[rule] = rule.spec, base, bin_name
        rules[name] = MappingProxyType(bin_aliases)
        try:
            definition = CoverPointDef(name, dict(sorted(bins.items())), source=None if field is None else ".".join(field.path),
                                       iff=gate(point.iff, name), weight=point.weight, goal=point.goal,
                                       overlap=point.overlap or point_options.get("overlap", OverlapPolicy.WARN),
                                       description=point_options.get("description", ""))
        except ValueError as error:
            _fail(model, name, "E_SCHEMA", str(error))
        point_defs.append(definition)
        reviews.append({"name": name, "field": [] if field is None else list(field.path), "value_type": "event" if temporal else _type_id(value_type),
                        "iff": None if definition.iff is None else definition.iff.to_dict(),
                        "goal": point.goal, "weight": point.weight,
                        "origin": _origin(origins[name]), "bins": [
                            {"name": key, **spec.to_dict(), "origin": _origin(bin_origins[key])}
                            for key, spec in sorted(bins.items())]})
        if temporal:
            executions = {}
            for key, spec in sorted(bins.items()):
                identity = spec.matcher.execution_key()
                executions.setdefault(identity, {"owner": key, "definition": pattern_definitions[(name, key)].__name__,
                                                   "bins": []})["bins"].append(key)
            reviews[-1]["executions"] = list(executions.values())

    def combinations(entries, slots, cross_name):
        if entries is None:
            return None
        output = []
        for entry in entries:
            if len(entry) != len(slots):
                _fail(model, cross_name, "E_CROSS", "selection dimension count differs from Cross")
            names = []
            for slot, selection in zip(slots, entry):
                if not isinstance(selection, BinSelection) or aliases.get(selection.point) != slot or selection.rule not in rules[slot]:
                    _fail(model, cross_name, "E_CROSS", "bin selection has wrong ownership or dimension order")
                names.append(rules[slot][selection.rule])
            output.append(tuple(names))
        return tuple(output)

    for name, cross in sorted(members.items()):
        if not isinstance(cross, Cross):
            continue
        try:
            slots = tuple(aliases[point] for point in cross.points)
        except KeyError:
            _fail(model, name, "E_CROSS", "Cross references a point outside this group")
        try:
            cross_defs.append(CrossDef(name, slots, include=combinations(cross.include, slots, name),
                                       ignore=combinations(cross.ignore, slots, name),
                                       illegal=combinations(cross.illegal, slots, name),
                                       iff=gate(cross.iff, name), weight=cross.weight, goal=cross.goal,
                                       max_auto_bins=cross.max_auto_bins))
        except ValueError as error:
            _fail(model, name, "E_CROSS", str(error))
    schema_id = options.get("schema_id") or _type_id(model)
    contract = options.get("contract") or schema_id
    if not isinstance(schema_id, str) or not schema_id or not isinstance(contract, str) or not contract:
        _fail(model, "identity", "E_IDENTITY", "schema_id and contract must be nonempty strings")
    try:
        schema = CoverGroupDef(schema_id, tuple(point_defs), tuple(cross_defs),
                               iff=gate(options.get("iff"), "iff"), description=options.get("description", ""))
    except ValueError as error:
        _fail(model, "schema", "E_SCHEMA", str(error))
    input_data = {"frontend": "declarative-v2-experiment", "sample": shape,
                  "observer_contract_explicit": bool(options.get("contract")),
                  "bindings": {name: list(point._source_parts) for name, point in
                               ((point.name, point) for point in point_defs if not point.is_pattern)}}
    if live:
        input_data["live_fields"] = [list(path) for path in sorted(used_fields)]
    review = {"schema_id": schema_id, "sample_type": _type_id(sample_type), "points": reviews,
              "iff": None if schema.iff is None else schema.iff.to_dict(), "contract": contract,
              "crosses": [{**cross.definition.to_dict(), "origin": _origin(origins[cross.definition.name]),
                           "eligible_bins": len(cross.eligible)} for cross in schema._resolved_crosses]}
    compiled = CompiledGroup(schema, sample_type, contract, sha256(_json(input_data).encode()).hexdigest(),
                             _json(input_data), _json(review), MappingProxyType(aliases),
                             MappingProxyType(cross_aliases), MappingProxyType(rules),
                             tuple(used_fields[key] for key in sorted(used_fields)),
                             MappingProxyType(pattern_definitions) if live else _validation_plan(sample_type))
    _CACHE[model] = (_signature(model), compiled)
    return compiled
