"""Compiled static sampling caches accessors, never values or X/Z state."""
from types import SimpleNamespace

import pytest
from toffee import Bundle, LogicValue


class Signal:
    def __init__(self, width, value=0, xmask=0):
        self.width, self.value, self.xmask = width, value, xmask
        self.width_reads = 0

    def W(self):
        self.width_reads += 1
        return self.width

    def U(self):
        return self.value

    def XMask(self):
        return self.xmask


def test_static_sampler_preserves_tree_and_returns_fresh_immutable_values():
    narrow, wide = Signal(5, 3), Signal(80, (1 << 79) + 7)
    bundle = Bundle(a=narrow, nested=Bundle(array=(wide, narrow)))
    sample = bundle.compile_sampler()
    width_reads = narrow.width_reads + wide.width_reads
    first = sample()
    assert first == bundle.sample()
    width_reads = narrow.width_reads + wide.width_reads
    narrow.value, narrow.xmask, wide.value = 15, 2, 0
    second = sample()
    assert narrow.width_reads + wide.width_reads == width_reads
    assert first.a == LogicValue(3, 0, 5)
    assert second.a == LogicValue(15, 2, 5)
    assert second.nested.array[0] == LogicValue(0, 0, 80)
    with pytest.raises(TypeError):
        second['a'] = 1


def test_wide_aval_bval_and_padding_are_identical_to_dynamic_sampling():
    class Wide(Signal):
        def U(self):
            raise AssertionError('wide aval/bval must not use unsigned getter')
        def GetBytes(self):
            return ((1 << 95) | self.value).to_bytes(12, 'little')
        def GetBvalBytes(self):
            return ((1 << 94) | self.xmask).to_bytes(12, 'little')
    signal = Wide(80, (1 << 79) | 3, (1 << 78) | 1)
    bundle = Bundle(payload=signal)
    sample = bundle.compile_sampler()
    assert sample() == bundle.sample()
    assert sample().payload == LogicValue(signal.value, signal.xmask, 80)
    signal.value, signal.xmask = 7, 0
    assert sample() == bundle.sample()


def test_width_inference_is_dynamic_without_explicit_width():
    signal = SimpleNamespace(value=1)
    sample = Bundle(value_signal=signal).compile_sampler()
    assert sample().value_signal.width == 1
    signal.value = 128
    assert sample().value_signal.width == 8


def test_zero_width_native_scalar_and_logic_value_getter_match_legacy():
    signal = Signal(0, 1)
    bundle = Bundle(s=signal)
    assert bundle.compile_sampler()() == bundle.sample()
    signal = Signal(8, LogicValue(3, 2, 4))
    bundle = Bundle(s=signal)
    with pytest.raises(ValueError):
        bundle.compile_sampler()()
    with pytest.raises(ValueError):
        bundle.sample()
    signal.value = LogicValue(3, 0, 4)
    assert bundle.compile_sampler()() == bundle.sample()


def test_recompile_after_rebinding_getters_or_width():
    signal = Signal(4, 3)
    bundle = Bundle(s=signal)
    old_sample = bundle.compile_sampler()
    signal.width = 8
    signal.U = lambda: 99
    sample = bundle.compile_sampler()
    assert sample() == bundle.sample()
    assert old_sample().s == LogicValue(3, 0, 4)


def test_native_xdata_wide_and_narrow():
    import xspcomm
    narrow = xspcomm.XData(8, xspcomm.XData.In)
    wide = xspcomm.XData(128, xspcomm.XData.In)
    bundle = Bundle(narrow=narrow, wide=wide)
    sample = bundle.compile_sampler()
    for n, w in [(1, 1 << 100), (127, (1 << 127) | 9), (0, 0)]:
        narrow.Set(n)
        wide.Set(w)
        assert sample() == bundle.sample()
