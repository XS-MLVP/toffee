"""Typed authoring API backed by the existing CoverGroup and native collector."""
from __future__ import annotations

from collections.abc import Sequence, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Generic, Literal, Self, TypeVar

from ..coverage import CoverGroup as CoreCoverGroup, IllegalPolicy
from ..signals import signal_width
from ..triggers import CompiledTrigger, PythonPredicateTrigger, PhaseTrigger
from ._compiler import CompiledGroup, compile_group, _json
from ._declarations import (
    DefinitionError, SampleTypeError, SignalBindingBase, SignalBinding, normalize, scalar_type,
)

S = TypeVar("S")


def _sampling_shape(trigger, abort, fields, explicit_contract, overlap, max_active):
    """Keep serializable temporal meaning in the contract, outside native IDs."""
    from .._coverage_runtime import _shape
    phase = trigger if isinstance(trigger, PhaseTrigger) else getattr(trigger, "sample", None)
    mode = getattr(trigger, "mode", None)
    result = {"kind": type(trigger).__name__, "phase": None if phase is None else type(phase).__name__,
              "mode": getattr(mode, "value", None), "overlap": overlap, "max_active": max_active or 1}
    if isinstance(trigger, CompiledTrigger):
        try:
            result["program"] = _shape(trigger.program)
        except ValueError as error:
            if not explicit_contract:
                raise DefinitionError("E_CONTRACT", trigger.name, "opaque pattern requires an explicit observer contract") from error
            result["program"] = {"opaque": True, "name": trigger.name}
    elif isinstance(trigger, PythonPredicateTrigger):
        if not explicit_contract:
            raise DefinitionError("E_CONTRACT", trigger.name, "Python predicate requires an explicit observer contract")
        result["program"] = {"opaque": True, "name": trigger.name}
    if abort is not None:
        abort_field = next((name for name, signal in fields.items() if signal is abort), None)
        if abort_field is not None:
            result["abort"] = {"field": abort_field}
        else:
            try:
                result["abort"] = _shape(abort)
            except ValueError as error:
                if not explicit_contract:
                    raise DefinitionError("E_CONTRACT", "abort", "opaque abort source requires an explicit observer contract") from error
                result["abort"] = {"opaque": True}
    return result


def _validated_snapshot(value, plan, path):
    expected, children = plan
    if type(value) is not expected:
        raise SampleTypeError(f"{path}: expected {expected.__name__}, got {type(value).__name__}")
    if scalar_type(expected):
        return normalize(value)
    return {name: _validated_snapshot(getattr(value, name), child, f"{path}.{name}")
            for name, child in children}


@dataclass(frozen=True)
class CoverageSnapshot:
    _json: str

    @property
    def samples(self) -> int:
        return json.loads(self._json)["samples"]

    @property
    def coverage(self) -> float:
        return json.loads(self._json)["coverage"]

    def to_json(self) -> str:
        return self._json

    def to_dict(self) -> dict:
        return json.loads(self._json)


class CoverGroup(Generic[S]):
    """Typed declaration frontend owning exactly one existing runtime engine."""

    def __init_subclass__(cls, **options):
        super().__init_subclass__()
        allowed = {"schema_id", "contract", "iff"}
        if options.keys() - allowed:
            raise TypeError(f"unknown coverage class options: {sorted(options.keys() - allowed)}")
        cls._coverage_group_options = dict(options)

    def __init__(self, *, instance: str, illegal_policy: IllegalPolicy = IllegalPolicy.RAISE,
                 run_id: str | None = None) -> None:
        self._compiled = type(self).compile()
        self._engine = self._compiled.schema.instantiate(instance, illegal_policy=illegal_policy,
                                                         run_id=run_id, contract=self._compiled.sampling_contract)
        self._binding_json: str | None = None

    @classmethod
    def compile(cls) -> CompiledGroup[S]:
        return compile_group(cls)

    @property
    def runtime(self) -> CoreCoverGroup:
        """The sole counter store, also passed to Execution and CoverageDatabase."""
        return self._engine

    @property
    def samples(self) -> int:
        self.sync()
        return self._engine.samples

    @property
    def gated(self) -> int:
        self.sync()
        return self._engine.gated

    @property
    def coverage(self) -> float:
        return self._engine.coverage

    def sync(self) -> None:
        self._engine.sync()

    def reset(self) -> None:
        self._engine.reset()

    def clear_history(self) -> None:
        self._engine.clear_history()

    def report(self) -> dict:
        return self._engine.report()

    def sample(self, sample: S) -> None:
        snapshot = _validated_snapshot(sample, self._compiled._validation, type(self).__name__)
        self._engine.sample(snapshot, details=False)

    def snapshot(self) -> CoverageSnapshot:
        return CoverageSnapshot(json.dumps(self.report(), ensure_ascii=False, sort_keys=True))

    def binding_contract_json(self) -> str | None:
        """Readable physical bindings without signal pointers or engine IDs."""
        return self._binding_json

    def bind(self, *, trigger: object, fields: Sequence[SignalBindingBase],
             strategy: Literal["auto", "native", "python"] = "auto", abort: object | None = None,
             accumulate: bool = False, overlap: bool = False,
             max_active: int | None = None) -> Self:
        if self._engine._collector is not None:
            raise RuntimeError("cannot rebind active coverage")
        expected = {field.path: field for field in self._compiled._fields}
        configured, bindings = {}, []
        for binding in fields:
            if not isinstance(binding, SignalBinding):
                raise DefinitionError("E_BINDING", type(self).__name__, "use wire(field_ref, signal)")
            field = binding.field
            if field.root is not self._compiled.sample_type or field.path not in expected:
                raise DefinitionError("E_BINDING_ROOT", ".".join(field.path), "field is not used by this group")
            if field.value_type is not expected[field.path].value_type:
                raise DefinitionError("E_BINDING_TYPE", ".".join(field.path), "field type differs from model")
            if field.value_type not in (int, bool):
                raise DefinitionError("E_BINDING_TYPE", ".".join(field.path),
                                      "clock-bound v2 inputs currently require int or bool")
            name = ".".join(field.path)
            if name in configured:
                raise DefinitionError("E_DUPLICATE_BINDING", name, "field is bound twice")
            width = signal_width(binding.source)
            if field.value_type is bool and width != 1:
                raise DefinitionError("E_BINDING_WIDTH", name, "bool requires exactly one physical bit")
            if binding.source_id is not None and (not isinstance(binding.source_id, str) or not binding.source_id):
                raise DefinitionError("E_SOURCE_ID", name, "source_id must be a nonempty stable string")
            configured[name] = binding.source
            bindings.append({"field": name, "width": width, "source_id": binding.source_id})
        missing = {".".join(path) for path in expected} - configured.keys()
        if missing:
            raise DefinitionError("E_MISSING_BINDING", type(self).__name__, f"unbound fields: {sorted(missing)}")
        explicit_contract = json.loads(self._compiled._input_json)["observer_contract_explicit"]
        if any(item["source_id"] is None for item in bindings) and not explicit_contract:
            raise DefinitionError("E_CONTRACT", type(self).__name__,
                                  "opaque signal identities require an explicit versioned observer contract")
        sampling = _sampling_shape(trigger, abort, configured, explicit_contract, overlap, max_active)
        # Include the temporal program; two Within windows are different observations.
        binding_digest = sha256(_json({"bindings": sorted(bindings, key=lambda item: item["field"]),
                                      "sampling": sampling}).encode()).hexdigest()
        contract = f"{self._compiled.sampling_contract}|binding={binding_digest}"
        self._engine.bind(trigger=trigger, fields=configured, strategy=strategy, abort=abort,
                          accumulate=accumulate, contract=contract, overlap=overlap, max_active=max_active)
        self._binding_json = json.dumps({"binding_digest": binding_digest,
                                        "observer_contract": self._compiled.contract,
                                        "sampling": sampling,
                                        "bindings": sorted(bindings, key=lambda item: item["field"]),
                                        "opaque_sources": sorted(item["field"] for item in bindings if item["source_id"] is None)},
                                       ensure_ascii=False, indent=2, sort_keys=True)
        return self


class SignalCoverGroup(CoverGroup[S]):
    """Live declaration frontend; inherits the single existing counter store."""
    def sample(self, sample: S) -> None:
        raise TypeError("SignalCoverGroup samples live programs inside Execution")

    def bind(self, *, pins: S, sample: PhaseTrigger,
             strategy: Literal["native", "python", "auto"] = "auto", abort: object = None,
             accumulate: bool = False, source_ids: Mapping[str, str] | None = None,
             diagnostics: Literal["off", "summary"] = "off") -> Self:
        from ..ir import resolve_path
        if self._engine._collector is not None:
            raise RuntimeError("cannot rebind active coverage")
        if not isinstance(pins, self._compiled.sample_type):
            raise DefinitionError("E_BINDING_ROOT", type(self).__name__, "live root has another type")
        for definition in self._compiled._validation.values():
            definition.bind(pins, sample=sample)
        sources = {".".join(field.path): resolve_path(pins, field.path) for field in self._compiled._fields}
        for field in self._compiled._fields:
            if field.value_type is bool and signal_width(sources[".".join(field.path)]) != 1:
                raise DefinitionError("E_BINDING_WIDTH", ".".join(field.path), "bool requires exactly one physical bit")
        identities = source_ids or {}
        for name, identity in identities.items():
            if name not in sources or not isinstance(identity, str) or not identity:
                raise DefinitionError("E_SOURCE_ID", name, "use stable nonempty identities for used fields")
        explicit = json.loads(self._compiled._input_json)["observer_contract_explicit"]
        if not explicit and any(name not in identities for name in sources):
            raise DefinitionError("E_CONTRACT", type(self).__name__, "provide source_ids or explicit observer contract")
        bindings = [{"field": name, "width": signal_width(source), "source_id": identities.get(name)} for name, source in sorted(sources.items())]
        sampling = _sampling_shape(sample, abort, sources, explicit, False, 1)
        sampling["bindings"] = bindings
        digest = sha256(_json(sampling).encode()).hexdigest()
        contract = f"{self._compiled.sampling_contract}|binding={digest}"
        self._engine.bind(trigger=sample, fields=sources, strategy=strategy, abort=abort,
                          accumulate=accumulate, contract=contract, root=pins, diagnostics=diagnostics)
        self._binding_json = json.dumps({"binding_digest": digest, "sampling": sampling}, sort_keys=True)
        return self
