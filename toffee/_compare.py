__all__ = ["compare_once", "compare_once_monitor", "Comparator"]

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

def compare_once_monitor(dut_item, std_item, compare=None, match_detail=False):
    if compare is None:
        compare = __default_compare

    if not compare(dut_item, std_item):
        error(
            f"Mismatch\n----- STDOUT -----\n{std_item}\n----- DUTOUT -----\n{dut_item}\n------------------"
        )

        # Asynchronous fixture and tasks in monitor cause this bug
        # Temporarily use the following solution
        asyncio.get_event_loop().stop()
    else:
        if match_detail:
            info(
                f"Match\n----- STDOUT -----\n{std_item}\n----- DUTOUT -----\n{dut_item}\n------------------"
            )
        else:
            info("Match")
        return True


class Comparator(Component):
    def __init__(self, dut_port, model_ports, compare=None, match_detail=False):
        super().__init__()
        self.dut_port = dut_port
        self.model_ports = model_ports
        self.compare = compare
        self.match_detail = match_detail

    async def main(self):
        while True:
            dut_item = await self.dut_port.get()
            for port in self.model_ports:
                std_item = await port.get()
                compare_once(dut_item, std_item, self.compare, self.match_detail)
