"""Pure one-to-one association and single-clock deadline arithmetic."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Hashable

from .interfaces import Transfer
from .transfers import XTransfer


@dataclass(frozen=True)
class Association:
    latency_cycles: int | None
    request_key: Callable[[Any], Hashable] | None
    response_key: Callable[[Any], Hashable] | None

    def select(
        self, observation: Transfer[Any], pending: Sequence[XTransfer[Any, Any]]
    ) -> XTransfer[Any, Any] | None:
        if self.request_key is not None and self.response_key is not None:
            key = self.response_key(observation.value)
            return next(
                (item for item in pending if self.request_key(item.request) == key),
                None,
            )
        if self.latency_cycles is not None:
            return next(
                (item for item in pending
                 if item.accepted_event is not None
                 and observation.event.tick == cycle_deadline(
                     item.accepted_event.tick, self.latency_cycles
                 )),
                None,
            )
        return pending[0] if pending else None


def cycle_deadline(tick: int, cycles: int) -> int:
    # XClock uses two stable half-ticks per cycle. All timed observations and
    # acceptances are rising-stable; keep this assumption out of matchers.
    return tick + cycles * 2
