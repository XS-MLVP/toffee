"""Positive static typing fixtures; API sketch, not executable coverage.

api.pyi deliberately has no runtime module. Use Pyright, not `python usage.py`.
"""

from dataclasses import dataclass
from enum import Enum, unique
from typing import assert_type

from api import (
    Bin,
    BinRule,
    BoundCross,
    BoundPoint,
    CompiledGroup,
    CoverGroup,
    CoverPoint,
    Cross,
    Fields,
    Iff,
)


@unique
class Source(Enum):
    MAIN = 0
    AHEAD = 1


@dataclass(frozen=True)
class PredictionSample:
    source: Source
    latency: int
    checked: bool


class SourcePoint(CoverPoint[Source]):
    # Optional annotations also check each bin declaration against its domain.
    main: BinRule[Source] = Bin.values(Source.MAIN)
    ahead: BinRule[Source] = Bin.values(Source.AHEAD)


class LatencyPoint(CoverPoint[int]):
    short: BinRule[int] = Bin.range(0, 3)
    long: BinRule[int] = Bin.range(4, 15)
    invalid: BinRule[int] = Bin.illegal(Bin.range(-32, -1))


class PredictionCoverage(
    CoverGroup[PredictionSample],
    schema_id="bpu.prediction",
    contract="bpu.observed-prediction/v1",
):
    # Implicit binding: group slot name -> sample field, checked by compile().
    source = SourcePoint()
    latency = LatencyPoint()
    source_latency = Cross(source, latency)


class StrictLatencyPoint(LatencyPoint):
    long: BinRule[int] = Bin.range(4, 7)
    timeout: BinRule[int] = Bin.illegal(Bin.range(8, 15))


class StrictPredictionCoverage(PredictionCoverage):
    latency = StrictLatencyPoint()
    # The inherited source_latency is rebound to THIS latency slot by compile().


@dataclass(frozen=True)
class Envelope:
    prediction: PredictionSample
    output_source: Source


envelope_fields = Fields(Envelope)


class EnvelopeCoverage(
    CoverGroup[Envelope],
    schema_id="bpu.prediction-envelope",
    contract="bpu.observed-envelope/v1",
):
    # The lambda receives a typed Envelope for completion. It only builds a path.
    source = SourcePoint(
        source=envelope_fields.select(lambda sample: sample.prediction.source)
    )
    result = SourcePoint(
        source=envelope_fields.select(lambda sample: sample.output_source)
    )
    latency = LatencyPoint(
        source=envelope_fields.select(lambda sample: sample.prediction.latency)
    )
    source_result = Cross(source, result)
    source_latency = Cross(
        source,
        latency,
        ignore=((source.bin(SourcePoint.ahead), latency.bin(LatencyPoint.long)),),
    )


prediction_fields = Fields(PredictionSample)


class CheckedPredictionCoverage(
    PredictionCoverage,
    schema_id="bpu.checked-prediction",
    contract="bpu.checked-prediction/v1",
    iff=Iff.truth(prediction_fields.select(lambda sample: sample.checked)),
):
    """A separate metric; checked must be supplied by the checking pipeline."""


def consume_observation(coverage: PredictionCoverage, observed: PredictionSample) -> None:
    # The Monitor/collector, not a testcase phase name, supplies this observation.
    coverage.sample(observed)


def inspect_types() -> None:
    coverage = PredictionCoverage(instance="kunminghu_v3.bpu")
    other = PredictionCoverage(instance="kunminghu_v3.bpu_replica")
    strict = StrictPredictionCoverage(instance="kunminghu_v3.strict_bpu")

    assert_type(PredictionCoverage.source, SourcePoint)
    assert_type(PredictionCoverage.latency, LatencyPoint)
    assert_type(coverage.source, BoundPoint[Source])
    assert_type(coverage.latency, BoundPoint[int])
    assert_type(coverage.source_latency, BoundCross)
    assert_type(coverage.source.count(SourcePoint.main), int)
    assert_type(other.source.count(SourcePoint.main), int)
    assert_type(StrictPredictionCoverage.latency, StrictLatencyPoint)
    assert_type(strict.latency, BoundPoint[int])
    assert_type(strict.latency.count(StrictLatencyPoint.timeout), int)
    assert_type(PredictionCoverage.compile(), CompiledGroup[PredictionSample])
    assert_type(StrictPredictionCoverage.compile(), CompiledGroup[PredictionSample])
    assert_type(EnvelopeCoverage.compile(), CompiledGroup[Envelope])
    assert_type(CheckedPredictionCoverage.compile(), CompiledGroup[PredictionSample])

    assert_type(
        coverage.source_latency.count(
            PredictionCoverage.source.bin(SourcePoint.main),
            PredictionCoverage.latency.bin(LatencyPoint.long),
        ),
        int,
    )
