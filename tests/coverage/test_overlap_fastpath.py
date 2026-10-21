"""Exact-bin overlap optimization preserves pairs, order and policy failures."""
from random import Random
import pytest
import xreactor.coverage as module
from xreactor import Bin, CoverPointDef


def original(named):
    normal = [item for item in named if item.spec.kind is module.BinKind.NORMAL]
    return tuple((left.name, right.name) for index, left in enumerate(normal)
        for right in normal[index + 1:]
        if module._matcher_overlaps(left.spec.matcher, right.spec.matcher))


def test_random_exact_bins_match_original_pair_order_and_boolean_aliases():
    random = Random(1024)
    values = [None, False, True, 2, 3, '0', '1', '2']
    for _ in range(200):
        named = tuple(module.NamedBin(str(i), Bin.values(*random.sample(values, random.randrange(1, 5))))
            for i in range(random.randrange(1, 12)))
        assert module._normal_overlaps(named) == original(named)


def test_ignore_illegal_and_default_bins_do_not_add_normal_overlap():
    named = (module.NamedBin('a', Bin.values(1, 2)), module.NamedBin('b', Bin.values(2, 3)),
        module.NamedBin('ignored', Bin.ignore(Bin.values(2))),
        module.NamedBin('illegal', Bin.illegal(Bin.values(2))),
        module.NamedBin('default', Bin.default()))
    assert module._normal_overlaps(named) == original(named) == (('a', 'b'),)


def test_mixed_matchers_retain_the_general_algorithm():
    named = (module.NamedBin('a', Bin.values(1)), module.NamedBin('b', Bin.range(0, 2)),
        module.NamedBin('c', Bin.masked(1, 1)))
    assert module._normal_overlaps(named) == original(named)


def test_overlap_warning_and_error_are_not_disabled():
    bins = {'a': Bin.values(1, 2), 'b': Bin.values(2, 3)}
    with pytest.warns(UserWarning, match='a/b'):
        CoverPointDef('point', bins, overlap='warn')
    with pytest.raises(ValueError, match='a/b'):
        CoverPointDef('point', bins, overlap='error')
