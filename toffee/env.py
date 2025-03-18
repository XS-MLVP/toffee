__all__ = ["Env"]

from .agent import Agent
from ._base import MObject
from .logger import error
from .logger import warning
from .model import Model


class Env(MObject):
    """
    Env is used to wrap the entire verification environment and provides reference model synchronization
    """

    def __init__(self):
        self.attached_models = []

    def __init_subclass__(cls, **kwargs):
        """
        Do some initialization when subclassing.
        """

        super().__init_subclass__(**kwargs)
        original_init = cls.__init__

        def new_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.__config_agent_name()

        cls.__init__ = new_init

    def attach(self, model):
        """
        Attach a model to the env.

        Args:
            model: The model to be attached.

        Returns:
            The env itself.
        """

        assert isinstance(model, Model), f"Model {model} is not an instance of Model"
        model.collect_all()

        if model.is_attached():
            warning(
                f"Model {model} is already attached to an env, the original env will be replaced"
            )
            model.attached_env.unattach(model)

        self.__inject_all(model)
        self.__ensure_model_match(model)
        self.attached_models.append(model)

        return self

    def unattach(self, model):
        """
        Unattach a model from the env.

        Args:
            model: The model to be unattached.

        Returns:
            The env itself.
        """

        if model in self.attached_model:
            self.__uninject_all(model)
            self.attached_model.remove(model)
            model.clear_matched()
            model.attached_env = None
        else:
            error(f"Model {model} is not attached to the env")

        return self

    def all_agent_names(self):
        """
        Yields all agent names in the env.

        Returns:
            A generator that yields all agent names in the env.
        """

        for attr in dir(self):
            if isinstance(getattr(self, attr), Agent):
                yield attr

    def __config_agent_name(self):
        """
        Configure all Driver and Monitor in the agents.
        """

        # Set the agent name to all Driver and Method
        for agent_name in self.all_agent_names():
            agent = getattr(self, agent_name)

            for driver_method in agent.all_driver_method():
                driver = self.__get_driver(agent_name, driver_method.__name__)
                driver.agent_name = agent_name
                driver.path = f"{agent_name}.{driver.name}"

            for monitor_method in agent.all_monitor_method():
                monitor = self.__get_monitor(agent_name, monitor_method.__name__)
                monitor.agent_name = agent_name
                monitor.path = f"{agent_name}.{monitor.name}"

    # Inject and uninject methods

    @staticmethod
    def __is_agent_hook_contain_method(
        agent_hook, agent_name_of_method: str, method_name: str
    ) -> bool:
        if (
            agent_name_of_method == agent_hook.__agent_name__
            or agent_name_of_method in agent_hook.__agents__
        ):
            agent_hook.__matched__[0] = True
            return True
        elif f"{agent_name_of_method}.{method_name}" in agent_hook.__methods__:
            agent_hook.__matched__[0] = True
            agent_hook.__methods_matched__[
                agent_hook.__methods__.index(f"{agent_name_of_method}.{method_name}")
            ] = True
            return True
        return False

    @staticmethod
    def __is_agent_port_contain_method(
        agent_port, agent_name_of_method: str, method_name: str
    ) -> bool:
        if (
            agent_name_of_method == agent_port.get_path()
            or agent_name_of_method in agent_port.agents
        ):
            agent_port.matched = True
            return True
        elif f"{agent_name_of_method}.{method_name}" in agent_port.methods:
            agent_port.matched = True
            agent_port.methods_matched[
                agent_port.methods.index(f"{agent_name_of_method}.{method_name}")
            ] = True
            return True
        return False

    @staticmethod
    def __all_agent_hooks_contain_method(
        model: Model, agent_name_of_method: str, method_name: str
    ) -> list:
        return [
            hook
            for hook in model.all_agent_hooks
            if Env.__is_agent_hook_contain_method(
                hook, agent_name_of_method, method_name
            )
        ]

    @staticmethod
    def __all_agent_ports_contain_method(
        model: Model, agent_name_of_method: str, method_name: str
    ) -> list:
        return [
            port
            for port in model.all_agent_ports
            if Env.__is_agent_port_contain_method(
                port, agent_name_of_method, method_name
            )
        ]

    def __inject_driver_method(self, model: Model, agent_name, driver_method):
        """
        Inject hook and port from model to matched driver method.
        """

        driver_path = f"{agent_name}.{driver_method.__name__}"

        model_info = {
            "agent_hook": Env.__all_agent_hooks_contain_method(
                model, agent_name, driver_method.__name__
            ),
            "agent_port": Env.__all_agent_ports_contain_method(
                model, agent_name, driver_method.__name__
            ),
            "driver_hook": model.get_driver_hook(driver_path, mark_matched=True),
            "driver_port": model.get_driver_port(driver_path, mark_matched=True),
        }

        driver = self.__get_driver(agent_name, driver_method.__name__)
        driver.model_infos[model] = model_info

    def __inject_monitor_method(self, model: Model, agent_name, monitor_method):
        """
        Inject port from model to matched monitor method.
        """

        monitor_path = f"{agent_name}.{monitor_method.__name__}"

        model_info = {
            "agent_hook": Env.__all_agent_hooks_contain_method(
                model, agent_name, monitor_method.__name__
            ),
            "agent_port": Env.__all_agent_ports_contain_method(
                model, agent_name, monitor_method.__name__
            ),
            "monitor_hook": model.get_monitor_hook(monitor_path, mark_matched=True),
            "monitor_port": model.get_monitor_port(monitor_path, mark_matched=True),
        }

        monitor = self.__get_monitor(agent_name, monitor_method.__name__)
        monitor.model_infos[model] = model_info

    def __inject_all(self, model):
        """
        Inject all hooks and ports to the agents.
        """

        for agent_name in self.all_agent_names():
            agent = getattr(self, agent_name)

            for driver_method in agent.all_driver_method():
                self.__inject_driver_method(model, agent_name, driver_method)

            for monitor_method in agent.all_monitor_method():
                self.__inject_monitor_method(model, agent_name, monitor_method)

    def __uninject_all(self, model):
        """
        Uninject all hooks and ports from the agents.
        """

        for agent_name in self.all_agent_names():
            agent = getattr(self, agent_name)

            for driver_method in agent.all_driver_method():
                driver = self.__get_driver(agent_name, driver_method.__name__)
                driver.model_infos.pop(model)

            for monitor_method in agent.all_monitor_method():
                monitor = self.__get_monitor(agent_name, monitor_method.__name__)
                monitor.model_infos.pop(model)

    def __ensure_model_match(self, model: Model):
        """
        Make sure the model matches the env. This function should be called after injecting.

        Args:
            model: The model to be checked.

        Raises:
            ValueError: If the model does not match the env.
        """

        model.ensure_all_matched()

        for agent_hook in model.all_agent_hooks:
            for agent in agent_hook.__agents__:
                if not hasattr(self, agent):
                    raise ValueError(
                        f"Agent hook {agent_hook.__name__} is not matched to agent {agent}"
                    )
            if not agent_hook.__agent_name__ == "" and not hasattr(
                self, agent_hook.__agent_name__
            ):
                raise ValueError(
                    f"Agent hook {agent_hook.__name__} is not matched to agent {agent_hook.__agent_name__}"
                )

        for agent_port in model.all_agent_ports:
            for agent in agent_port.agents:
                if not hasattr(self, agent):
                    raise ValueError(
                        f"Agent port {agent_port.__name__} is not matched to agent {agent}"
                    )
            if not agent_port.name == "" and not hasattr(self, agent_port.name):
                raise ValueError(
                    f"Agent port {agent_port.name} is not matched to agent {agent_port.name}"
                )

    def __get_driver(self, agent_name, driver_name):
        """
        Get the driver by name.
        """

        agent = getattr(self, agent_name)
        return agent.drivers[driver_name]

    def __get_monitor(self, agent_name, monitor_name):
        """
        Get the monitor by name.
        """

        agent = getattr(self, agent_name)
        return agent.monitors[monitor_name]
