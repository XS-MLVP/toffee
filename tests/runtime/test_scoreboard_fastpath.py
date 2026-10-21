"""Fast success must preserve structural comparison, not Python object equality."""
from dataclasses import dataclass, field
from random import Random

import pytest
import xreactor.scoreboard as module
from xreactor import Scoreboard, ScoreboardMismatch


@dataclass
class PartialEquality:
    value: int
    hidden: int = field(compare=False)


@pytest.mark.parametrize('expected,actual', (
    ({'a': [1, 2]}, {'a': (1, 2)}),
    ([True, 2.0], (1, 2)),
    (None, None),
    ({'a': []}, {'a': []}),
    ([float('nan')], [float('nan')]),
    (PartialEquality(1, 2), PartialEquality(1, 3)),
    ({'a': PartialEquality(1, 2)}, {'a': PartialEquality(1, 3)}),
    ({'a': [1, 2]}, {'a': [1, 3]}),
    ({'a': 1}, {'b': 1}),
    ([1, 2], [1]),
))
def test_fast_and_diagnostic_paths_are_equivalent(monkeypatch, expected, actual):
    fast = module.structural_differences(expected, actual)
    monkeypatch.setattr(module, '_plain_equal', lambda a, b: False)
    assert module.structural_differences(expected, actual) == fast


def test_identical_nan_in_same_container_is_still_a_mismatch():
    nan = float('nan')
    values = [nan]
    assert values == values
    assert module.structural_differences(values, values)


def test_different_types_of_dataclass_and_subclass_are_not_shortcut():
    @dataclass
    class Child(PartialEquality):
        pass
    assert PartialEquality(1, 2) != Child(1, 2)
    assert module.structural_differences(PartialEquality(1, 2), Child(1, 2))


def test_random_builtin_trees_match_original_walk(monkeypatch):
    random = Random(312)
    positive = module._plain_equal
    def tree(depth=0):
        if depth == 3 or random.randrange(3) == 0:
            return random.choice([None, True, False, 0, 1, 2, 'a', 'b'])
        if random.randrange(2):
            return {str(i): tree(depth + 1) for i in range(random.randrange(4))}
        values = [tree(depth + 1) for _ in range(random.randrange(4))]
        return tuple(values) if random.randrange(2) else values
    for _ in range(500):
        expected = tree()
        actual = tree() if random.randrange(2) else expected
        for limit in (0, 1, 20):
            monkeypatch.setattr(module, '_plain_equal', positive)
            fast = module.structural_differences(expected, actual, limit=limit)
            monkeypatch.setattr(module, '_plain_equal', lambda a, b: False)
            assert module.structural_differences(expected, actual, limit=limit) == fast


def test_mismatch_keeps_paths_status_and_context():
    board = Scoreboard('fast-path-regression')
    board.check(expected={'cells': [0] * 1024}, actual={'cells': [0] * 1024})
    wrong = [0] * 1024
    wrong[1023] = 1
    with pytest.raises(ScoreboardMismatch) as error:
        board.check(expected={'cells': [0] * 1024}, actual={'cells': wrong})
    assert error.value.differences[0].path == 'cells[1023]'
    assert error.value.status.checked == 2
    assert error.value.status.passed == 1
    assert error.value.status.failed == 1
