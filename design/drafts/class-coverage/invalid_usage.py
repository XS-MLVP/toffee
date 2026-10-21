"""Deliberate typing failures, excluded from the default positive check.

Every EXPECT_ERROR marks one line on which Pyright should report an error.
Runtime-only schema and ownership errors are listed in README.md instead.
"""

from enum import Enum

from api import Bin, BinRule, Fields
from usage import (
    Envelope,
    LatencyPoint,
    PredictionCoverage,
    PredictionSample,
    Source,
    SourcePoint,
)


class UnrelatedSource(Enum):
    MAIN = 0


def rejected_by_typing(coverage: PredictionCoverage) -> None:
    coverage.sample({"phase": "main_return_full"})  # EXPECT_ERROR
    PredictionSample(source="main", latency=2, checked=True)  # EXPECT_ERROR
    PredictionSample(source=UnrelatedSource.MAIN, latency=2, checked=True)  # EXPECT_ERROR
    coverage.source.count(LatencyPoint.short)  # EXPECT_ERROR
    coverage.source.count(SourcePoint.mian)  # EXPECT_ERROR
    SourcePoint(source=Fields(PredictionSample).select(lambda sample: sample.latency))  # EXPECT_ERROR
    Fields(PredictionSample).select(lambda sample: sample.latecny)  # EXPECT_ERROR
    Fields(Envelope).select(lambda sample: sample.prediction.soruce)  # EXPECT_ERROR
    bad_bin: BinRule[Source] = Bin.values(UnrelatedSource.MAIN)  # EXPECT_ERROR
    _ = bad_bin
    coverage.bind(trigger=object(), fields=(SourcePoint(),), strategy="native")  # EXPECT_ERROR
    coverage.bind(trigger=object(), fields=(), strategy="cpp")  # EXPECT_ERROR
