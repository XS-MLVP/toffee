__all__ = ["Delayer"]

import asyncio
from .asynchronous import add_callback
from ._base import MObject


async def __process_delayer():
    for delayer in asyncio.get_event_loop().delayer_list:
        delayer.sample()
    return False


add_callback(__process_delayer)


class Delayer(MObject):
    """
    A Delayer class is used to delay the signal value.
    """

    def __init__(self, signal, delay):
        super().__init__()

        assert delay >= 0, "Delay should be greater than or equal to 0."

        self.signal = signal
        self.delay = delay
        self.value_list = []

        asyncio.get_event_loop().delayer_list.append(self)

    def sample(self):
        """
        Sample the signal value once and store it in the value list.
        """

        if len(self.value_list) >= self.delay + 1:
            self.value_list.pop(0)

        self.value_list.append(self.signal.value)

    @property
    def value(self):
        """
        Get the value of the signal after the delay.
        """

        if len(self.value_list) != self.delay + 1:
            return None

        return self.value_list[0]
