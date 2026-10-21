from typing import assert_type

from xreactor import Transfer
from xreactor.declarative import BinRule, BoundPoint, FieldRef, Fields, SignalBinding, wire
from examples.coverage.declarative_protocol import (
    CompletedTransaction, CycleSnapshot, ProtocolBundle, ReadyPoint,
    RequestCycleCoverage, TransactionCollector, TransactionCoverage,
)

requests = RequestCycleCoverage(instance="typing.request")
transactions = TransactionCoverage(instance="typing.transactions")
collector = TransactionCollector()
fields = Fields(CycleSnapshot)

assert_type(requests.ready, BoundPoint[bool])
assert_type(ReadyPoint.waited_two_then_accepted, BinRule[bool])
assert_type(fields.select(lambda sample: sample.request.ready), FieldRef[bool])


def bind_ready(pins: ProtocolBundle) -> SignalBinding[bool]:
    return wire(fields.select(lambda sample: sample.request.ready), pins.request.ready)


def consume(observation: Transfer[CycleSnapshot]) -> None:
    assert_type(observation.value.request.ready, bool)
    completed = collector.observe(observation)
    assert_type(completed, CompletedTransaction | None)
    if completed is not None:
        transactions.sample(completed)
