"""Known-value width/signed/cast behavior agrees in Python and native execution."""
import asyncio
import random
from types import SimpleNamespace

import pytest

from xreactor import (
    bits, ClockCycles, Execution, FSM, RisingEdge, Sequence, State, Wait, Within,
    XCommClockBackend, xtrigger,
)
from xreactor.declarative import Bin, SignalCoverGroup, TemporalCoverPoint, covergroup
from xreactor.ir import BoolType, expression_type, program_from_dict, program_to_dict, signal_expr


@pytest.fixture
def native():
    x = pytest.importorskip("xspcomm")
    if not hasattr(x.XTriggerEngine, "ExprNewBits"):
        pytest.skip("requires draft xcomm typed expressions")
    return x


def decoded(backend, expression):
    kind = expression_type(expression, None)
    root = backend._lower_typed_expr(expression, None)
    result = int.from_bytes(bytes(backend._engine.ExprEvalBits(root)), "little")
    if isinstance(kind, BoolType):
        return bool(result)
    if kind.signed and result & (1 << (kind.width - 1)):
        result -= 1 << kind.width
    return result


@pytest.mark.parametrize("width", [1, 8, 9, 32, 64, 65, 128])
@pytest.mark.parametrize("signed", [False, True])
def test_native_typed_arithmetic_against_integer_reference(native, width, signed):
    clock = native.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    rng = random.Random(1203 + width)
    mask = (1 << width) - 1
    normalize = lambda n: ((n & mask) - (1 << width) if signed and (n & (1 << (width - 1))) else n & mask)
    try:
        for _ in range(20):
            a, b = normalize(rng.getrandbits(width)), normalize(rng.getrandbits(width))
            lhs, rhs = bits(a, width, signed=signed), bits(b, width, signed=signed)
            cases = [(lhs + rhs, normalize(a + b)), (lhs - rhs, normalize(a - b)),
                     (lhs * rhs, normalize(a * b)), (lhs.bit_and(rhs), normalize(a & b)),
                     (lhs.bit_or(rhs), normalize(a | b)), (lhs.bit_xor(rhs), normalize(a ^ b)),
                     (lhs.bit_not(), normalize(~a)), (-lhs, normalize(-a)),
                     (lhs < rhs, a < b), (lhs == rhs, a == b), (lhs >= rhs, a >= b)]
            if b:
                quotient = (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1)
                cases.extend([(lhs // rhs, normalize(quotient)), (lhs % rhs, normalize(a - quotient * b))])
            for amount in (0, width - 1, width, width + 1, 1 << 100):
                cases.extend([(lhs << amount, normalize(a << amount) if amount < width else 0),
                              (lhs >> amount, normalize(a >> amount) if amount < width else (-1 if a < 0 else 0))])
            for expression, expected in cases:
                assert expression.evaluate(None) == expected
                assert decoded(backend, expression) == expected
            backend.collect_programs()
    finally:
        backend.close()


def test_cast_rules_literal_checks_and_serialization(native):
    clock = native.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    try:
        signed = bits(255, 8).as_signed()
        cases = [(signed, -1), (signed.resize(16), -1), (signed.resize(16).as_unsigned(), 65535),
                 (signed.as_unsigned().resize(16), 255), (bits(0x1234, 16).resize(8), 0x34),
                 (bits(-128, 8, signed=True).resize(128), -128),
                 ((signed < 0), True), ((bits(255, 8) + 1) == 0, True)]
        for expression, expected in cases:
            restored = program_from_dict(program_to_dict(expression))
            assert expression.evaluate(None) == restored.evaluate(None) == decoded(backend, expression) == expected
        for value, width, signed in [(-1, 8, False), (256, 8, False), (128, 8, True), (-129, 8, True)]:
            with pytest.raises(ValueError):
                bits(value, width, signed=signed)
        for expression in [bits(1, 8) + bits(1, 16), bits(1, 8) < bits(1, 8, signed=True),
                           bits(1, 8) == True]:
            with pytest.raises(TypeError):
                expression_type(expression, None)
        for expression in [bits(1, 8) << -1, bits(1, 8) + 256]:
            with pytest.raises(ValueError):
                expression_type(expression, None)
        with pytest.raises(ZeroDivisionError):
            (bits(1, 8) // 0).evaluate(None)
        with pytest.raises(RuntimeError, match="division by zero"):
            decoded(backend, bits(1, 8) // 0)
        assert decoded(backend, True | ((bits(1, 8) // 0) == 1)) is True
    finally:
        backend.close()


@pytest.mark.parametrize("width", [0, 1, 65, 128])
def test_typed_bound_signals_include_picker_scalar_representation(native, width):
    clock = native.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    signal = native.XData(width, native.XData.InOut)
    signal.Set(1)
    try:
        expression = signal_expr(signal).resize(129) + 1
        assert expression.evaluate(None) == decoded(backend, expression) == 2
        if width <= 1:
            signed = signal_expr(signal).as_signed()
            assert signed.evaluate(None) == decoded(backend, signed) == -1
    finally:
        backend.close()


@xtrigger(sample=RisingEdge("clock"))
def arithmetic_roundtrip(pins: SimpleNamespace):
    return Sequence(Wait(signal_expr(pins.value).as_signed() < 0),
                    Within(1, 2, (signal_expr(pins.value).resize(129) + 1) == 1 << 128))


@xtrigger(sample=RisingEdge("clock"))
def signed_terminal(pins: SimpleNamespace):
    return FSM(start="idle", states={
        "idle": State().when(signal_expr(pins.value).as_signed() == -1).goto("pending"),
        "pending": State().when((signal_expr(pins.value).bit_and(255)) == 255).trigger("OK")
            .otherwise().trigger("BAD"),
    })


class TypedPoint(TemporalCoverPoint[SimpleNamespace]):
    roundtrip = arithmetic_roundtrip
    good = Bin.pattern(signed_terminal, terminals=("OK",))
    bad = Bin.pattern(signed_terminal, terminals=("BAD",))


@covergroup(contract="test.typed-bins/v1")
class TypedCoverage(SignalCoverGroup[SimpleNamespace]):
    values = TypedPoint()


@pytest.mark.parametrize("strategy", ["python", "native"])
def test_typed_programs_compile_inside_shared_fsm_and_sequence_bins(native, strategy):
    async def run():
        clock = native.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)
        value = native.XData(128, native.XData.InOut)
        pins = SimpleNamespace(clock=clock, value=value)
        group = TypedCoverage(instance="typed")
        group.bind(pins=pins, sample=RisingEdge(clock), strategy=strategy)
        try:
            async with Execution(backend, coverage=[group.runtime]):
                value.Set((1 << 128) - 1)
                await ClockCycles(clock, 1)
                backend.collect_programs()
                await ClockCycles(clock, 1)
                assert group.values.count(TypedPoint.roundtrip) == group.values.count(TypedPoint.good) == 1
                assert group.values.count(TypedPoint.bad) == 0
                if strategy == "native":
                    collector = group.runtime._collector
                    assert backend._engine.CoverageExecutionCount(collector._native_handle()) == 2
            return group.report()
        finally:
            backend.close()
    report = asyncio.run(run())
    assert report["sampling_backend"] == strategy
