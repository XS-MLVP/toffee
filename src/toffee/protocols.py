from __future__ import annotations

from collections.abc import Callable
import inspect
from typing import Any

from ._reactor_context import current_reactor
from .data import iter_data_leaves
from .events import XEvent, XPhase
from .signals import write_signal
from .triggers import DriveStable, FallingEdge, RisingEdge, Value


def _refresh_comb() -> None:
    backend = current_reactor().backend
    refresh = getattr(backend, "refresh_comb", None)
    if refresh is not None:
        refresh()


def _commit_rise_write(signal: Any) -> bool:
    is_rise = getattr(signal, "IsRiseWrite", None)
    if not callable(is_rise) or not is_rise():
        return False
    signal.WriteOnRise()
    return True


async def drive_ready_valid(
    clock: Any,
    valid: Any,
    ready: Any,
    drive: Callable[[], None],
    *,
    bits: Any | None = None,
    admit: Callable[[], bool] | None = None,
) -> XEvent:
    """Drive one ready/valid transaction and return its accepting rising edge.

    Payload and ``valid`` are driven in a falling-stable phase.  A picker
    Rise-written ``valid`` is committed before DriveStable arbitration and
    withdrawn immediately after the accepting edge, without changing its
    configured write mode. Pass ``bits`` when payload uses deferred picker
    writes; the helper commits its Rise-written leaves before arbitration.
    Payload and ``valid`` remain stable through non-accepting rising edges.

    ``drive`` must be synchronous: allowing it to await would lose the stable
    drive phase and make transaction ownership ambiguous.

    Optional ``admit`` is evaluated in each available drive phase, before any
    encoding or writes. False defers this transaction by a cycle; true admits
    it atomically with ``drive`` (no intervening await). It must synchronously
    return a bool. This gates launch, not acceptance: after launch, normal
    ready/valid backpressure applies and admission is not re-evaluated.
    """

    reactor = current_reactor()
    if getattr(reactor.backend, "phase", None) is not XPhase.FALLING_STABLE:
        await FallingEdge(clock)

    if admit is not None:
        while True:
            admitted = admit()
            if inspect.isawaitable(admitted):
                close = getattr(admitted, "close", None)
                if close is not None:
                    close()
                raise TypeError("admit callback must be synchronous and return bool")
            if type(admitted) is not bool:
                raise TypeError("admit callback must return bool")
            if admitted:
                break
            await FallingEdge(clock)

    drive_result = drive()
    if inspect.isawaitable(drive_result):
        close = getattr(drive_result, "close", None)
        if close is not None:
            close()
        raise TypeError("drive callback must be synchronous and return None")
    if drive_result is not None:
        raise TypeError("drive callback must return None")
    valid_asserted = False
    try:
        if bits is not None:
            for _, signal in iter_data_leaves(bits, "bits"):
                _commit_rise_write(signal)
        write_signal(valid, 1, "valid")
        valid_asserted = True
        # Picker's Set alone only stages a Rise-mode pin. Arbitration must
        # see valid at DriveStable, before the accepting RTL edge.
        _commit_rise_write(valid)
        _refresh_comb()
        # Another producer may still change arbitration after this coroutine's
        # drive. Sample once all drives have settled, even if ready is high now.
        # Backpressure stays in the native engine without per-cycle Python wakes.
        await Value(ready, 1, sample=DriveStable(clock))
        return await RisingEdge(clock)
    finally:
        if valid_asserted:
            write_signal(valid, 0, "valid")
            if callable(getattr(valid, "IsRiseWrite", None)) and valid.IsRiseWrite():
                # A queued zero would leave valid high for the next rising
                # edge and allow the same beat to be accepted twice.
                valid.ImmSet(0)
            _refresh_comb()
