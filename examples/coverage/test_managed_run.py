"""toffee run examples/coverage/test_managed_run.py --workers 2"""
import pytest

from toffee import Bin, CoverGroupDef, CoverPointDef


@pytest.mark.parametrize("value", [0, 1, 0, 1])
def test_observed_results(value):
    group = CoverGroupDef("results", (
        CoverPointDef("value", {"zero": Bin.values(0), "one": Bin.values(1)}),
    )).instantiate("device.results")
    group.sample({"value": value}, details=False)
    # No export, worker identity, collector or merge code is needed.
