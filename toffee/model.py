__all__ = [
    "Model",
    "agent_hook",
    "driver_hook",
    "monitor_hook",
    "AgentPort",
    "DriverPort",
    "MonitorPort",
]

from .asynchronous import Component
from .asynchronous import Queue
from .logger import warning, error


def agent_hook(agent_name: str = ""):
    """
    Decorator for agent hook.

    Args:
        agent_name: The name of the agent to be hooked. If it is empty, the name of the function will be used.
    """

    def decorator(func):
        nonlocal agent_name

        if agent_name == "":
            agent_name = func.__name__

        func.__is_agent_hook__ = True
        func.__agent_name__ = agent_name
        func.__matched__ = [False]

        return func

    return decorator


def driver_hook(driver_path: str = "", *, agent_name: str = "", driver_name: str = ""):
    """
    Decorator for driver hook.

    Args:
        driver_path: The path of the driver.
        agent_name:  The name of the agent to be hooked.
        driver_name: The name of the driver to be hooked.
    """

    assert driver_path == "" or (
        agent_name == "" and driver_name == ""
    ), "agent_name and driver_name must be empty when driver_path is set"

    assert (
        agent_name != "" or driver_name == ""
    ), "agent_name must not be empty when driver_name is set"

    def decorator(func):
        nonlocal driver_path, agent_name, driver_name

        if driver_path == "":
            if agent_name != "":
                if driver_name != "":
                    driver_path = f"{agent_name}.{driver_name}"
                else:
                    driver_path = f"{agent_name}.{func.__name__}"
            else:
                driver_path = func.__name__.replace("__", ".")

        func.__is_driver_hook__ = True
        func.__driver_path__ = driver_path
        func.__matched__ = [False]

        return func

    return decorator

def monitor_hook(monitor_path: str = "", agent_name: str = "", monitor_name: str = ""):
    """
    Decorator for monitor hook.

    Args:
        monitor_path: The path of the monitor.
        agent_name:   The name of the agent to be hooked.
        monitor_name: The name of the monitor to be hooked.
    """

    assert monitor_path == "" or (
        agent_name == "" and monitor_name == ""
    ), "agent_name and monitor_name must be empty when monitor_path is set"

    assert (
        agent_name != "" or monitor_name == ""
    ), "agent_name must not be empty when monitor_name is set"

    def decorator(func):
        nonlocal monitor_path, agent_name, monitor_name

        if monitor_path == "":
            if agent_name != "":
                if monitor_name != "":
                    monitor_path = f"{agent_name}.{monitor_name}"
                else:
                    monitor_path = f"{agent_name}.{func.__name__}"
            else:
                monitor_path = func.__name__.replace("__", ".")

        func.__is_monitor_hook__ = True
        func.__monitor_path__ = monitor_path
        func.__matched__ = [False]

        return func

    return decorator


class Port(Queue):
    def __init__(self, name: str = "", maxsize: int = 4):
        """
        Args:
            name:    The name of the port.
            maxsize: The maximum size of the port. if it is -1, the port is unbounded.
        """

        super().__init__()
        assert maxsize > 0 or maxsize == -1 , "maxsize must be greater than 0 or equal to -1"

        self.name = name
        self.matched = False
        self.__maxsize = maxsize

    async def put(self, value):
        if self.qsize() >= self.__maxsize and self.__maxsize != -1:
            error(f"Port attached to {self.name} is full, the value {value} is dropped")
            return

        await super().put(value)

class DriverPort(Port):
    """
    A The DriverPort is used to match the DriverPort of the agent, and it is used to accept requests from the agent.
    """

    def __init__(
        self, driver_path: str = "", *, agent_name: str = "", driver_name: str = "", maxsize: int = 4
    ):
        assert driver_path == "" or (
            agent_name == "" and driver_name == ""
        ), "agent_name and driver_name must be empty when driver_path is set"

        assert (
            agent_name != "" or driver_name == ""
        ), "agent_name must not be empty when driver_name is set"

        self.driver_path = driver_path
        self.agent_name = agent_name
        self.driver_name = driver_name

        super().__init__(name=self.get_path(), maxsize=maxsize)

    def get_path(self):
        """Get the driver path."""

        if self.driver_path == "":
            if self.agent_name != "":
                if self.driver_name != "":
                    self.driver_path = f"{self.agent_name}.{self.driver_name}"
                else:
                    self.driver_path = f"{self.agent_name}.{self.name}"
            else:
                self.driver_path = self.name.replace("__", ".")

        return self.driver_path

    async def __call__(self):
        return await self.get()


class AgentPort(Port):
    async def __call__(self):
        return await self.get()


class MonitorPort(Port):
    """
    The MonitorPort is used to match the MonitorPort in the agent, and it is used for the Model to send the results
    out and compare them with the results in the agent.
    """

    def __init__(
        self, monitor_path: str = "", *, agent_name: str = "", monitor_name: str = "", maxsize: int = 4
    ):
        assert monitor_path == "" or (
            agent_name == "" and monitor_name == ""
        ), "agent_name and monitor_name must be empty when monitor_path is set"

        assert (
            agent_name != "" or monitor_name == ""
        ), "agent_name must not be empty when monitor_name is set"

        self.monitor_path = monitor_path
        self.agent_name = agent_name
        self.monitor_name = monitor_name

        super().__init__(name=self.get_path(), maxsize=maxsize)

    def get_path(self):
        """Get the monitor path."""

        if self.monitor_path == "":
            if self.agent_name != "":
                if self.monitor_name != "":
                    self.monitor_path = f"{self.agent_name}.{self.monitor_name}"
                else:
                    self.monitor_path = f"{self.agent_name}.{self.name}"
            else:
                self.monitor_path = self.name.replace("__", ".")

        return self.monitor_path

    async def __call__(self):
        return await self.get()


class Model(Component):
    """
    The Model is used to provide a reference model for the DUT.
    """

    def __init__(self):
        super().__init__()
        self.attached_agent = None

        self.all_agent_ports = []
        self.all_driver_ports = []
        self.all_monitor_ports = []

        self.all_agent_hooks = []
        self.all_driver_hooks = []
        self.all_monitor_hooks = []

    def collect_all(self):
        """
        Collect all driver ports, monitor ports, driver hooks, and agent hooks.
        """

        self.all_agent_ports.clear()
        self.all_driver_ports.clear()
        self.all_monitor_ports.clear()
        self.all_agent_hooks.clear()
        self.all_driver_hooks.clear()
        self.all_monitor_hooks.clear()

        for attr in dir(self):
            attr_value = getattr(self, attr)

            if isinstance(attr_value, Port):
                if attr_value.name == "":
                    attr_value.name = attr

                if isinstance(attr_value, DriverPort):
                    self.all_driver_ports.append(attr_value)
                elif isinstance(attr_value, MonitorPort):
                    self.all_monitor_ports.append(attr_value)
                elif isinstance(attr_value, AgentPort):
                    self.all_agent_ports.append(attr_value)

            elif callable(attr_value):
                if hasattr(attr_value, "__is_driver_hook__"):
                    self.all_driver_hooks.append(attr_value)
                elif hasattr(attr_value, "__is_agent_hook__"):
                    self.all_agent_hooks.append(attr_value)
                elif hasattr(attr_value, "__is_monitor_hook__"):
                    self.all_monitor_hooks.append(attr_value)

    def clear_matched(self):
        """
        Clear the matched status of all driver ports, monitor ports, driver hooks, and agent hooks.
        """

        for driver_port in self.all_driver_ports:
            driver_port.matched = False

        for monitor_port in self.all_monitor_ports:
            monitor_port.matched = False

        for driver_hook in self.all_driver_hooks:
            driver_hook.__matched__[0] = False

        for agent_hook in self.all_agent_hooks:
            agent_hook.__matched__[0] = False

        for monitor_hook in self.all_monitor_hooks:
            monitor_hook.__matched__[0] = False

    def is_attached(self):
        """
        Check if the model is attached to an agent.
        """

        return self.attached_agent is not None

    def ensure_all_matched(self):
        """
        Ensure all driver ports, monitor ports, driver hooks, and agent hooks are matched.
        """

        for agent_hook in self.all_agent_hooks:
            if not agent_hook.__matched__[0]:
                raise ValueError(
                    f"Agent hook {agent_hook.__agent_name__} is not matched"
                )

        for driver_hook in self.all_driver_hooks:
            if not driver_hook.__matched__[0]:
                raise ValueError(
                    f"Driver hook {driver_hook.__driver_path__} is not matched"
                )

        for monitor_hook in self.all_monitor_hooks:
            if not monitor_hook.__matched__[0]:
                raise ValueError(
                    f"Monitor hook {monitor_hook.__monitor_path__} is not matched"
                )

        for agent_port in self.all_agent_ports:
            if not agent_port.matched:
                raise ValueError(
                    f"Agent port {agent_port.name} is not matched"
                )

        for driver_port in self.all_driver_ports:
            if not driver_port.matched:
                raise ValueError(
                    f"Driver port {driver_port.get_path()} is not matched"
                )

        for monitor_port in self.all_monitor_ports:
            if not monitor_port.matched:
                raise ValueError(
                    f"Monitor port {monitor_port.get_path()} is not matched"
                )

    def get_driver_port(self, drive_path: str, mark_matched: bool = False):
        """
        Get the driver port by name.
        """

        for driver_port in self.all_driver_ports:
            if driver_port.get_path() == drive_path:
                if mark_matched:
                    driver_port.matched = True
                return driver_port

    def get_monitor_port(self, monitor_path: str, mark_matched: bool = False):
        """
        Get the monitor port by name.
        """

        for monitor_port in self.all_monitor_ports:
            if monitor_port.get_path() == monitor_path:
                if mark_matched:
                    monitor_port.matched = True
                return monitor_port

    def get_driver_hook(self, driver_path: str, mark_matched: bool = False):
        """
        Get the driver hook by name.
        """

        for driver_hook in self.all_driver_hooks:
            if driver_hook.__driver_path__ == driver_path:
                if mark_matched:
                    driver_hook.__matched__[0] = True
                return driver_hook

    def get_agent_port(self, name: str, mark_matched: bool = False):
        """
        Get the agent port by name.
        """

        for agent_port in self.all_agent_ports:
            if agent_port.name == name:
                if mark_matched:
                    agent_port.matched = True
                return agent_port

    def get_agent_hook(self, name: str, mark_matched: bool = False):
        """
        Get the agent hook by name.
        """

        for agent_hook in self.all_agent_hooks:
            if agent_hook.__agent_name__ == name:
                if mark_matched:
                    agent_hook.__matched__[0] = True
                return agent_hook

    def get_monitor_hook(self, monitor_path: str, mark_matched: bool = False):
        """
        Get the monitor hook by name.
        """

        for monitor_hook in self.all_monitor_hooks:
            if monitor_hook.__monitor_path__ == monitor_path:
                if mark_matched:
                    monitor_hook.__matched__[0] = True
                return monitor_hook

    async def main(self): ...
