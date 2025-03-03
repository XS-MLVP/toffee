__all__ = [
    "Driver",
    "Monitor",
]

import inspect
from .asynchronous import create_task
from .asynchronous import Event
from .asynchronous import Queue
from ._compare import compare_once
from .executor import add_priority_task
from .logger import warning


class BaseAgent:
    def __init__(self, func, compare_func):
        self.func = func
        self.name = func.__name__
        self.agent_name = ""
        self.compare_func = compare_func
        self.model_infos = {}


class Driver(BaseAgent):
    """
    The Driver is used to drive the DUT and forward the driver information to
    the reference model.
    """

    def __init__(self, drive_func):
        super().__init__(drive_func, None)

        self.sche_order = "parallel"
        self.priority = 99

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

    async def __drive_single_model(self, model_info, arg_list, kwarg_list):
        """
        Drive a single model.

        Args:
            model_info: The model information.
            arg_list: The list of args.
            kwarg_list: The list of kwargs.

        Returns:
            The result of the model.
        """

        if model_info["agent_port"] is not None:
            args_dict = self.__get_args_dict(arg_list, kwarg_list)

            await model_info["agent_port"].put((self.name, args_dict))

        if model_info["driver_port"] is not None:
            args_dict = self.__get_args_dict(arg_list, kwarg_list)
            args = next(iter(args_dict.values())) if len(args_dict) == 1 else args_dict

            await model_info["driver_port"].put(args)

        if model_info["agent_hook"] is not None:
            target = model_info["agent_hook"]
            args_dict = self.__get_args_dict(arg_list, kwarg_list)
            if inspect.iscoroutinefunction(target):
                result = await target(self.name, args_dict)
            else:
                result = target(self.name, args_dict)

            if model_info["driver_hook"] is None:
                return result

        if model_info["driver_hook"] is not None:
            target = model_info["driver_hook"]
            if inspect.iscoroutinefunction(target):
                result = await target(*arg_list, **kwarg_list)
            else:
                result = target(*arg_list, **kwarg_list)
            return result

    async def forward_to_models(self, arg_list, kwarg_list):
        """
        Forward the item to the models.

        Args:
            arg_list: The list of args.
            kwarg_list: The list of kwargs.
        """

        results = []
        for model_info in self.model_infos.values():
            results.append(
                await self.__drive_single_model(model_info, arg_list, kwarg_list)
            )

        return results

    def compare_results(self, dut_result, model_results):
        """
        Compare the result of the DUT and the models.

        Args:
            dut_result: The result of the DUT.
            model_results: The results of the models.
        """

        for model_result in model_results:
            if model_result is not None and dut_result is None:
                warning(
                    f"The model result is {model_result}, but the DUT result is None."
                )

            elif model_result is not None and dut_result is not None:
                compare_once(
                    dut_result, model_result, self.compare_func, match_detail=True
                )

    async def model_exec_wrapper(self, model_coro, results, compare_func):
        results["model_results"] = await model_coro

        if results["dut_result"] is not None:
            compare_func(results["dut_result"], results["model_results"])

    async def process_driver_call(self, agent, arg_list, kwarg_list):
        """
        Process the driver call.

        Args:
            arg_list: The list of args.
            kwarg_list: The list of kwargs.

        Returns:
            The result of the DUT if imme_ret is False, otherwise None.
        """

        results = {"dut_result": None, "model_results": None}

        model_coro = self.model_exec_wrapper(
            self.forward_to_models(arg_list, kwarg_list), results, self.compare_results
        )

        if self.sche_order == "parallel":
            model_done = Event()
            add_priority_task(model_coro, self.priority, model_done)

            results["dut_result"] = await self.func(agent, *arg_list, **kwarg_list)
            if results["model_results"] is not None:
                self.compare_results(results["dut_result"], results["model_results"])
            await model_done.wait()

        elif self.sche_order == "model_first":
            model_done = Event()
            add_priority_task(model_coro, self.priority, model_done)
            await model_done.wait()
            results["dut_result"] = await self.func(agent, *arg_list, **kwarg_list)
            self.compare_results(results["dut_result"], results["model_results"])

        elif self.sche_order == "dut_first":
            model_done = Event()
            results["dut_result"] = await self.func(agent, *arg_list, **kwarg_list)
            add_priority_task(model_coro, self.priority, model_done)
            await model_done.wait()

        else:
            raise ValueError(f"Invalid sche_order: {self.sche_order}")

        return results["dut_result"]


class Monitor(BaseAgent):
    """
    The Monitor is used to monitor the DUT and compare the output with the reference.
    """

    def __init__(self, agent, monitor_func):
        super().__init__(monitor_func, None)

        self.compare_queue = Queue()
        self.get_queue = Queue()
        self.agent = agent

        self.monitor_task = create_task(self.__monitor_forever())

    def get_queue_size(self):
        return self.get_queue.qsize()

    async def process_monitor_call(self, ret):
        for model_info in self.model_infos.values():
            if model_info["monitor_port"] is not None:
                await model_info["monitor_port"].put(ret)

            if model_info["monitor_hook"] is not None:
                model_info["monitor_hook"](ret)

            if model_info["agent_hook"] is not None:
                model_info["agent_hook"](self.agent, ret)

            if model_info["agent_port"] is not None:
                await model_info["agent_port"].put((self.name, ret))

    async def __monitor_forever(self):
        while True:
            await self.agent.monitor_step()

            ret = await self.func(self.agent)
            if ret is not None:
                add_priority_task(self.process_monitor_call(ret), 0)
                await self.get_queue.put(ret)
