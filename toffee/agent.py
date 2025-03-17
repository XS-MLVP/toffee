__all__ = [
    "Agent",
    "driver_method",
    "monitor_method",
]

from ._base_agent import Driver
from ._base_agent import Monitor
from .logger import warning


class Agent:
    """Provides an agent for operation on the DUT."""

    def __init__(self, bundle):
        """
        Args:
            bundle: The bundle of the agent to drive.
        """

        if callable(bundle):
            self.monitor_step = bundle
            warning(
                "Passing monitor_step during Agent initialization is about to be deprecated, so pass the Bundle instance directly."
            )
        else:
            self.bundle = bundle
            self.monitor_step = bundle.step

        self.drivers = {}
        self.monitors = {}
        self.__create_all_drivers()
        self.__create_all_monitors()

    def __create_all_drivers(self):
        """
        Create a driver for each driver method in the agent.
        """

        for driver_method in self.all_driver_method():
            driver = Driver(driver_method.__original_func__)
            self.drivers[driver_method.__name__] = driver

    def __create_all_monitors(self):
        """
        Create a monitor for each monitor method in the agent.
        """

        for monitor_method in self.all_monitor_method():
            monitor = Monitor(self, monitor_method.__original_func__)
            self.monitors[monitor_method.__name__] = monitor

    def monitor_size(self, monitor_name):
        """
        Get the queue size of the monitor.

        Args:
            monitor_name: The name of the monitor.

        Returns:
            The queue size of the monitor.
        """

        monitor = self.monitors[monitor_name]
        return monitor.get_queue_size()

    def start_monitor(self, monitor_name, maxsize=4):
        """
        After monitoring begins, monitor_method places the monitored data in a separate queue. Calling monitor_method
        in a test case will get the monitored data.

        Args:
            monitor_name: The name of the monitor.
            maxsize: The maximum size of the queue.
        """

        monitor = self.monitors[monitor_name]
        monitor.enable_get_queue(maxsize)

    def all_driver_method(self):
        """
        Yields all driver method in the agent.

        Returns:
            A generator that yields all driver method in the agent.
        """

        for attr in dir(self):
            if hasattr(getattr(self, attr), "__is_driver_decorated__"):
                yield getattr(self, attr)

    def all_monitor_method(self):
        """
        Yields all monitor methods in the agent.

        Returns:
            A generator that yields all monitor method in the agent.
        """

        for attr in dir(self):
            if hasattr(getattr(self, attr), "__is_monitor_decorated__"):
                yield getattr(self, attr)

    def get_driver_method(self, name):
        """Get the driver method by name."""

        if hasattr(self, name):
            driver_method = getattr(self, name)

            if hasattr(driver_method, "__is_driver_decorated__"):
                return driver_method

    def get_monitor_method(self, name):
        """Get the monitor method by name."""

        if hasattr(self, name):
            monitor_method = getattr(self, name)

            if hasattr(monitor_method, "__is_monitor_decorated__"):
                return monitor_method


import functools


def __driver_wrapped_func(func):
    func.__is_driver_decorated__ = True

    @functools.wraps(func)
    async def wrapper(agent, *args, **kwargs):
        driver = agent.drivers[func.__name__]
        return await driver.process_driver_call(agent, args, kwargs)

    wrapper.__original_func__ = func
    return wrapper


def driver_method():
    """
    Decorator for driver method.

    Returns:
        The decorator for driver method.
    """

    def decorator(func):
        return __driver_wrapped_func(func)

    return decorator


def __monitor_wrapped_func(func):
    func.__is_monitor_decorated__ = True

    @functools.wraps(func)
    async def wrapper(agent, *args, **kwargs):
        monitor = agent.monitors[func.__name__]
        return await monitor.get_queue.get()

    wrapper.__original_func__ = func
    return wrapper


def monitor_method():
    """
    Decorator for monitor method.

    Returns:
        The decorator for monitor method.
    """

    def decorator(func):
        return __monitor_wrapped_func(func)

    return decorator
