"""Class-based functional coverage declarations.

Public names are scoped to this namespace. The existing toffee coverage API
and its execution/counting implementation remain available.
"""
from ..coverage import IllegalPolicy, OverlapPolicy
from ._compiler import CompiledGroup
from ._declarations import (
    Bin, BinRule, PatternBin, BinSelection, BoundCross, BoundPoint, CoverPoint, PatternCoverPoint, TemporalCoverPoint,
    CoverageFragment, CoverageReferenceError, Cross, DefinitionError,
    FieldRef, Fields, Gate, Iff, PointDeclaration, SampleTypeError,
    SignalBinding, SignalBindingBase, covergroup, coverpoint, wire,
)
from ._runtime import CoverGroup, SignalCoverGroup, CoverageSnapshot

__all__ = [
    "Bin", "BinRule", "PatternBin", "BinSelection", "BoundCross", "BoundPoint", "CompiledGroup",
    "CoverGroup", "SignalCoverGroup", "CoverPoint", "PatternCoverPoint", "TemporalCoverPoint", "CoverageFragment", "CoverageReferenceError",
    "CoverageSnapshot", "Cross", "DefinitionError", "FieldRef", "Fields", "Gate",
    "Iff", "IllegalPolicy", "OverlapPolicy", "PointDeclaration", "SampleTypeError",
    "SignalBinding", "SignalBindingBase", "covergroup", "coverpoint", "wire",
]
