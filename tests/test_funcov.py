import os
import sys

import toffee.funcov as fc


class TextData(object):
    """Simple class to test funcov with a text value"""

    def __init__(self, value) -> None:
        self.value = value

    def __str__(self):
        return self.value


def test_funcov():
    v = TextData(1)
    g = fc.CovGroup("coverage_group_0")

    # 0.use default check functions
    g.add_watch_point(
        v,
        {
            "bin_name_is1": fc.Eq(1),
            "bin_name_range3-5": [fc.In([3, 4]), fc.In([3, 4, 5])],
        },
        name="watch_point_1",
    )

    # 1.use custom check functions in arg bins
    # check_fc is from arg check_func, defaut is None
    g.add_watch_point(
        v,
        {"bin_name_is2": lambda v: v.value == 2, "bin_name_range7-9": fc.In([7, 8, 9])},
        name="watch_point_2",
    )

    # 2.use custom check functions in arg check_func
    g.add_watch_point(
        v,
        {"bin_name_is3": fc.Eq(3), "bin_name_range1-5": fc.IsInRange(1, 5)},
        # check_func={
        #     "bin_name_is3": lambda v, conver_condition, points: conver_condition(v)
        # },
        name="watch_point_3",
    )

    # 3. use custom check functions to chech other bins be hinted
    def bin_name_is4_and_other_bins_are_all_hinted(v, conver_condition, points):
        if not conver_condition(v):
            return False
        checked = True
        for k, v in points["hints"].items():
            if k == "bin_name_is4_and_other_bins_are_all_hinted":
                continue
            if v < 1:
                checked = False
                break
        return checked

    g.add_watch_point(
        v,
        {
            "bin_name_is4_and_other_bins_are_all_hinted": fc.Eq(8),
            "bin_name_range1-5": fc.IsInRange(1, 5),
        },
        # check_func={
        #     "bin_name_is4_and_other_bins_are_all_hinted": bin_name_is4_and_other_bins_are_all_hinted
        # },
        name="watch_point_4",
    )

    g.sample()
    # test with different values
    v.value = 2
    g.sample()
    v.value = 3
    g.sample()
    v.value = 4
    g.sample()
    v.value = 5
    g.sample()
    v.value = 5
    assert g.is_all_covered() == False
    g.sample()
    v.value = 8
    g.sample()
    v.value = 8
    g.sample()
    v.value = 8
    g.sample()
    # print results
    assert g.is_all_covered() == True
    print("test_funcov pass pid: ", os.getpid(), file=sys.stderr)


def test_sample_assert(request):
    assert 1 == 1
    print("test_sample_assert pass pid: ", os.getpid(), file=sys.stderr)


if __name__ == "__main__":
    test_funcov()
