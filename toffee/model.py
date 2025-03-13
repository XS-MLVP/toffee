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


def agent_hook(
    agent_name: str = "",
    *,
    agents: list = [],
    methods: list = [],
    priority: int = 99,
    sche_order: str = "model_first",
):
    """
    Decorator for agent hook.

    Args:
        agent_name: The name of the agent to be hooked. If it is empty, the name of the function will be used.
        agents:     The list of agents to be hooked.
        methods:    The list of paths of the methods to be hooked.
        priority:   The priority of the agent hook which should be an integer between 0 and 99. The smaller the number,
                    the higher the priority. It will determine the order of calls within the same cycle. The default
                    priority of agent hook is 99.
        sche_order: The order in which the hook is called relative to the dut. If it is "model_first", it will be
                    called before the corresponding agent_method runs, and vice versa after it runs.
    """

    assert (
        agent_name == "" or agents == []
    ), "agent_name and agents cannot be set at the same time"
    assert priority in range(0, 100), "priority should be between 0 and 99"
    assert sche_order in [
        "model_first",
        "dut_first",
    ], "sche_order must be 'model_first' or 'dut_first'"

    def decorator(func):
        nonlocal agent_name

        if agent_name == "" and agents == [] and methods == []:
            agent_name = func.__name__

        func.__is_agent_hook__ = True
        func.__agent_name__ = agent_name
        func.__agents__ = agents
        func.__methods__ = methods
        func.__matched__ = [False]
        func.__methods_matched__ = [False] * len(methods)
        func.__priority__ = priority
        func.__sche_order__ = sche_order

        return func

    return decorator


def driver_hook(
    driver_path: str = "",
    *,
    agent_name: str = "",
    driver_name: str = "",
    priority=99,
    sche_order="model_first",
):
    """
    Decorator for driver hook.

    Args:
        driver_path: The path of the driver.
        agent_name:  The name of the agent to be hooked.
        driver_name: The name of the driver to be hooked.
        priority:    The priority of the driver hook which should be an integer between 0 and 99. The smaller the number,
                     the higher the priority. It will determine the order of calls within the same cycle. The default
                     priority of driver hook is 99.
        sche_order:  The order in which the hook is called relative to the dut. If it is "model_first", it will be
                     called before the corresponding driver_method runs, and vice versa after it runs.
    """

    assert driver_path == "" or (
        agent_name == "" and driver_name == ""
    ), "agent_name and driver_name must be empty when driver_path is set"

    assert (
        agent_name != "" or driver_name == ""
    ), "agent_name must not be empty when driver_name is set"

    assert priority in range(0, 100), "priority should be between 0 and 99"
    assert sche_order in [
        "model_first",
        "dut_first",
    ], "sche_order must be 'model_first' or 'dut_first'"

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
        func.__priority__ = priority
        func.__sche_order__ = sche_order

        return func

    return decorator


def monitor_hook(
    monitor_path: str = "",
    *,
    agent_name: str = "",
    monitor_name: str = "",
    priority: int = 0,
):
    """
    Decorator for monitor hook.

    Args:
        monitor_path: The path of the monitor.
        agent_name:   The name of the agent to be hooked.
        monitor_name: The name of the monitor to be hooked.
        priority:     The priority of the monitor hook which should be an integer between 0 and 99. The smaller the
                      number, the higher the priority.  It will use the same priority as driver_hook, determining the
                      order of calls within the same cycle. The default priority of monitor hook is -1, which means the
                      monitor hook will be executed before driver hook.
    """

    assert monitor_path == "" or (
        agent_name == "" and monitor_name == ""
    ), "agent_name and monitor_name must be empty when monitor_path is set"

    assert (
        agent_name != "" or monitor_name == ""
    ), "agent_name must not be empty when monitor_name is set"

    assert priority in range(0, 100), "priority should be between 0 and 99"

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
        func.__priority__ = priority
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
        assert (
            maxsize > 0 or maxsize == -1
        ), "maxsize must be greater than 0 or equal to -1"

        self.name = name
        self.matched = False
        self.__maxsize = maxsize

    async def put(self, value):
        if self.qsize() >= self.__maxsize and self.__maxsize != -1:
            error(f"port attached to {self.name} is full, the value {value} is dropped")
            return

        await super().put(value)


class DriverPort(Port):
    """
    The DriverPort is used to match driver_method in the agent and obtain call arguments.
    """

    def __init__(
        self,
        driver_path: str = "",
        *,
        agent_name: str = "",
        driver_name: str = "",
        maxsize: int = 4,
    ):
        assert driver_path == "" or (
            agent_name == "" and driver_name == ""
        ), "agent_name and driver_name must be empty when driver_path is set"

        assert (
            agent_name != "" or driver_name == ""
        ), "agent_name must not be empty when driver_name is set"

        super().__init__(maxsize=maxsize)

        self.driver_path = driver_path
        self.agent_name = agent_name
        self.driver_name = driver_name

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
    def __init__(
        self,
        agent_name: str = "",
        maxsize: int = 4,
        *,
        agents: list = [],
        methods: list = [],
    ):

        super().__init__(maxsize=maxsize)

        self.agent_name = agent_name
        self.agents = agents
        self.methods = methods
        self.methods_matched = [False] * len(methods)

    def get_path(self):
        return self.agent_name if self.agent_name != "" else self.name

    async def __call__(self):
        return await self.get()


class MonitorPort(Port):
    """
    The MonitorPort is used to match the monitor_method in the agent, and obtain the results.
    """

    def __init__(
        self,
        monitor_path: str = "",
        *,
        agent_name: str = "",
        monitor_name: str = "",
        maxsize: int = 4,
    ):
        assert monitor_path == "" or (
            agent_name == "" and monitor_name == ""
        ), "agent_name and monitor_name must be empty when monitor_path is set"

        assert (
            agent_name != "" or monitor_name == ""
        ), "agent_name must not be empty when monitor_name is set"

        super().__init__(maxsize=maxsize)

        self.monitor_path = monitor_path
        self.agent_name = agent_name
        self.monitor_name = monitor_name

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

        for monitor_hook in self.all_monitor_hooks:
            monitor_hook.__matched__[0] = False

        for agent_hook in self.all_agent_hooks:
            agent_hook.__matched__[0] = False
            agent_hook.__methods_matched__ = [False] * len(agent_hook.__methods__)

        for agent_port in self.all_agent_ports:
            agent_port.matched = False
            agent_port.methods_matched = [False] * len(agent_port.methods)

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
                warning(
                    f"Agent hook {agent_hook.__name__} is not matched to any method, please check it"
                )
            for method_matched in agent_hook.__methods_matched__:
                if not method_matched:
                    idx = agent_hook.__methods_matched__.index(method_matched)
                    raise ValueError(
                        f"Agent hook {agent_hook.__name__} is not matched to method {agent_hook.__methods__[idx]}"
                    )

        for driver_hook in self.all_driver_hooks:
            if not driver_hook.__matched__[0]:
                raise ValueError(f"Driver hook {driver_hook.__name__} is not matched")

        for monitor_hook in self.all_monitor_hooks:
            if not monitor_hook.__matched__[0]:
                raise ValueError(f"Monitor hook {monitor_hook.__name__} is not matched")

        for agent_port in self.all_agent_ports:
            if not agent_port.matched:
                raise ValueError(
                    f"Agent port {agent_port.name} is not matched to any agent, please check it"
                )
            for method_matched in agent_port.methods_matched:
                if not method_matched:
                    idx = agent_port.methods_matched.index(method_matched)
                    raise ValueError(
                        f"Agent port {agent_port.name} is not matched to method {agent_port.methods[idx]}"
                    )

        for driver_port in self.all_driver_ports:
            if not driver_port.matched:
                raise ValueError(f"Driver port {driver_port.get_path()} is not matched")

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
