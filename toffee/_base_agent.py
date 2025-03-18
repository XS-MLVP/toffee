__all__ = [
    "Driver",
    "Monitor",
]

import inspect
from .asynchronous import create_task
from .asynchronous import Event
from .asynchronous import Queue
from .asynchronous import gather
from ._compare import compare_once
from .executor import add_priority_task
from .logger import error


class BaseAgent:
    def __init__(self, func, compare_func):
        self.func = func
        self.name = func.__name__
        self.agent_name = ""
        self.path_name = ""
        self.compare_func = compare_func
        self.model_infos = {}


class Driver(BaseAgent):
    """
    The Driver is used to drive the DUT and forward the driver information to
    the reference model.
    """

    def __init__(self, drive_func):
        super().__init__(drive_func, None)

        self.sche_order = None
        self.priority = None

    def __get_args_dict(self, arg_list, kwarg_list):
        """
        Get the args and kwargs in the form of dictionary.

        Args:
            arg_list: The list of args.
            kwarg_list: The list of kwargs.

        Returns:
            The args and kwargs in the form of dictionary.
        """

        signature = inspect.signature(self.func)
        bound_args = signature.bind(None, *arg_list, **kwarg_list)
        bound_args.apply_defaults()
        arguments = bound_args.arguments
        del arguments["self"]
        return arguments

    async def __drive_single_model_ports(self, model_info, arg_list, kwarg_list):
        for agent_port in model_info["agent_port"]:
            args_dict = self.__get_args_dict(arg_list, kwarg_list)
            await agent_port.put((self.path, args_dict))

        if model_info["driver_port"] is not None:
            args_dict = self.__get_args_dict(arg_list, kwarg_list)
            args = next(iter(args_dict.values())) if len(args_dict) == 1 else args_dict
            await model_info["driver_port"].put(args)

    def __drive_single_driver_hook(
        self, driver_hook, model_results, arg_list, kwarg_list
    ):
        if inspect.iscoroutinefunction(driver_hook):
            assert False, "driver_hook should not be a coroutine function"

        async def driver_hook_wrapper():
            model_results.append((driver_hook, driver_hook(*arg_list, **kwarg_list)))

        event = Event()
        priority = (
            self.priority if self.priority is not None else driver_hook.__priority__
        )
        add_priority_task(driver_hook_wrapper(), priority, event)

        return event

    def __drive_single_agent_hook(
        self, agent_hook, model_results, arg_list, kwarg_list
    ):
        if inspect.iscoroutinefunction(agent_hook):
            assert False, "agent_hook should not be a coroutine function"

        async def agent_hook_wrapper():
            model_results.append(
                (
                    agent_hook,
                    agent_hook(self.path, self.__get_args_dict(arg_list, kwarg_list)),
                )
            )

        event = Event()
        priority = (
            self.priority if self.priority is not None else agent_hook.__priority__
        )
        add_priority_task(agent_hook_wrapper(), priority, event)

        return event

    async def process_driver_call(self, agent, arg_list, kwarg_list):
        """
        Process the driver call.

        Args:
            arg_list: The list of args.
            kwarg_list: The list of kwargs.

        Returns:
            The result of the DUT if imme_ret is False, otherwise None.
        """

        # Execute model_first driver hooks and agent hooks

        model_results = []
        model_first_events = []

        dut_first_driver_hooks = []
        dut_first_agent_hooks = []

        for _, model_info in self.model_infos.items():
            if model_info["driver_port"] or model_info["agent_port"]:
                await self.__drive_single_model_ports(model_info, arg_list, kwarg_list)
            else:
                if driver_hook := model_info["driver_hook"]:
                    if (
                        driver_hook.__sche_order__ == "model_first"
                        and self.sche_order != "dut_first"
                    ) or self.sche_order == "model_first":
                        model_first_events.append(
                            self.__drive_single_driver_hook(
                                driver_hook, model_results, arg_list, kwarg_list
                            ).wait()
                        )
                    else:
                        dut_first_driver_hooks.append(driver_hook)

                for agent_hook in model_info["agent_hook"]:
                    if (
                        agent_hook.__sche_order__ == "model_first"
                        and self.sche_order != "dut_first"
                    ) or self.sche_order == "model_first":
                        model_first_events.append(
                            self.__drive_single_agent_hook(
                                agent_hook, model_results, arg_list, kwarg_list
                            ).wait()
                        )
                    else:
                        dut_first_agent_hooks.append(agent_hook)

        await gather(*model_first_events)

        # Execute driver method

        dut_result = await self.func(agent, *arg_list, **kwarg_list)

        # Execute dut_first driver hooks and agent hooks
        async def background_exec():
            dut_first_events = []
            for driver_hook in dut_first_driver_hooks:
                dut_first_events.append(
                    self.__drive_single_driver_hook(
                        driver_hook, model_results, arg_list, kwarg_list
                    ).wait()
                )
            for agent_hook in dut_first_agent_hooks:
                dut_first_events.append(
                    self.__drive_single_agent_hook(
                        agent_hook, model_results, arg_list, kwarg_list
                    ).wait()
                )
            await gather(*dut_first_events)

            # Compare the results
            for model_result in model_results:
                if model_result[1] is not None:
                    compare_once(
                        dut_result,
                        model_result[1],
                        self.compare_func,
                        match_detail=True,
                    )

            self.priority = None
            self.sche_order = None

        create_task(background_exec())

        return dut_result


class Monitor(BaseAgent):
    """
    The Monitor is used to monitor the DUT and compare the output with the reference.
    """

    def __init__(self, agent, monitor_func):
        super().__init__(monitor_func, None)

        self.get_queue = None
        self.agent = agent

        self.monitor_task = create_task(self.__monitor_forever())

    def enable_get_queue(self, maxsize):
        self.get_queue = Queue()
        self.get_queue_max_size = maxsize

    def get_queue_size(self):
        return self.get_queue.qsize() if self.get_queue is not None else 0

    async def process_monitor_call(self, ret):
        async def async_wrapper(func, *args, **kwargs):
            return func(*args, **kwargs)

        for model_info in self.model_infos.values():
            for agent_port in model_info["agent_port"]:
                await agent_port.put((self.path, ret))

            if model_info["monitor_port"]:
                await model_info["monitor_port"].put(ret)

            for agent_hook in model_info["agent_hook"]:
                add_priority_task(
                    async_wrapper(agent_hook, self.path, ret), agent_hook.__priority__
                )

            if monitor_hook := model_info["monitor_hook"]:
                add_priority_task(
                    async_wrapper(monitor_hook, ret), monitor_hook.__priority__
                )

    async def __monitor_forever(self):
        while True:
            await self.agent.monitor_step()

            ret = await self.func(self.agent)
            if ret is not None:
                await self.process_monitor_call(ret)

                if self.get_queue is not None:
                    if self.get_queue.qsize() >= self.get_queue_max_size:
                        error(
                            f"the get_queue in {self.path} is full, the value {ret} is dropped"
                        )
                        continue
                    await self.get_queue.put(ret)
