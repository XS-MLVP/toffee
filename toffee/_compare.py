__all__ = ["compare_once"]

from .asynchronous import Component
from .asynchronous import asyncio
from .logger import *


def __default_compare(item1, item2):
    return item1 == item2


def compare_once(dut_item, std_item, compare=None, match_detail=False):
    if compare is None:
        compare = __default_compare

    if not compare(dut_item, std_item):
        error(
            f"Mismatch\n----- STDOUT -----\n{std_item}\n----- DUTOUT -----\n{dut_item}\n------------------"
        )
        assert False, f"mismatch: {dut_item} != {std_item}"
    else:
        if match_detail:
            info(
                f"Match\n----- STDOUT -----\n{std_item}\n----- DUTOUT -----\n{dut_item}\n------------------"
            )
        else:
            info("Match")
        return True
