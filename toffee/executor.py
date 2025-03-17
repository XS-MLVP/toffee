__all__ = ["Executor"]

from .asynchronous import add_callback
from .asynchronous import create_task
from .asynchronous import Event
from .asynchronous import gather
from ._base import MObject


"""
Priority Task Execution
"""

__priority_tasks = []


def add_priority_task(coro, priority, done_event=None):
    """
    Add a priority task to the priority task list.
    """

    __priority_tasks.append((coro, priority, done_event))


async def __execute_priority_tasks():
    """
    Execute the priority tasks in the priority task list. It will be called every clock cycle.
    """

    set_event = False
    for coro, _, done_event in sorted(__priority_tasks, key=lambda x: x[1]):
        await coro
        if done_event is not None:
            set_event = True
            done_event.set()

    __priority_tasks.clear()
    return set_event


add_callback(__execute_priority_tasks)

"""
Executor
"""


class Executor(MObject):
    """
    The executor class is used to manage the execution of multiple coroutines.
    """

    def __init__(self, exit="all"):
        """
        Args:
            exit: The exit condition of the executor. It can be "all", "none", or "any". If it is "all", the executor
                  will wait for all coroutines to complete. If it is "none", the executor will not wait for any
                  coroutine to complete. If it is "any", the executor will wait until any coroutine completes.
        """

        self.exit = exit

        self.__coros = {}
        self.__results = {}
        self.__uncompleted = []
        self.__exit_any_event = Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__execute()

    async def __execute(self):
        """
        Execute the coroutines and wait for them to complete.
        """

        if self.exit == "all":
            return await self.__exit_all()
        elif self.exit == "none":
            return await self.__exit_none()
        elif self.exit == "any":
            return await self.__exit_any()
        else:
            raise ValueError(f"Invalid exit option: {self.exit}")

    def __set_results(self, results):
        """
        Parse the results and set them to the results dictionary.

        Args:
            results: The results of the coroutines. It should be a list of result list.
        """

        result_dict = {}
        for i, tasks in enumerate(self.__coros.items()):
            if len(tasks[1]) == 1:
                result_dict[tasks[0]] = results[i][0]
            else:
                result_dict[tasks[0]] = results[i]

        self.__results = result_dict

    def __get_sche_group(self):
        """
        Create a coroutine for each group of coroutines and return the coroutine list.
        """

        sche_groups = []
        for tasks in self.__coros.items():
            sche_groups.append(self.__sequential_execution_all(*tasks[1]))
        return sche_groups

    async def __exit_all(self):
        """
        Execute all coroutines and wait for them to complete.
        """

        sche_groups = self.__get_sche_group()
        results = await gather(*sche_groups)
        self.__set_results(results)
        return self.__results

    async def __exit_none(self):
        """
        Execute all coroutines and do not wait for them to complete.
        """

        sche_groups = self.__get_sche_group()
        for coro in sche_groups:
            self.__uncompleted.append(create_task(coro))

    async def __exit_any(self):
        """
        Execute all coroutines and wait for any of them to complete.
        """

        self.__exit_any_event.clear()
        for tasks in self.__coros.items():
            self.__uncompleted.append(
                create_task(
                    self.__sequential_execution_all(
                        *tasks[1], complete_event=self.__exit_any_event
                    )
                )
            )
        await self.__exit_any_event.wait()

    async def wait_all(self):
        """
        Wait for all the coroutines to complete. This function should be called after execute.
        """

        if len(self.__uncompleted) == 0:
            return

        results = []
        for task in self.__uncompleted:
            results.append(await task)
        self.__set_results(results)

    def get_results(self):
        """
        Get the results of the coroutines.

        Returns:
            A dictionary of results.
        """

        return self.__results

    def __call__(self, coro, priority=None, sche_order=None, sche_group=None):
        """
        Add a coroutine to the executor.

        Args:
            coro: The coroutine to be added.
            priority: The priority of the coroutine. It should be an integer. The smaller the number, the higher the
                      priority. The default priority is 99. The priority is only valid for driver functions, if a
                      driver function has a higher priority, it will execute its reference model function first when
                      multiple driver functions are called in the same clock cycle.
            sche_group: The group name of the coroutine. The default group name is the coroutine name, if the group
                        name is the same, the coroutines will be executed sequentially.
        """

        if sche_group is None:
            sche_group = coro.__name__

            driver = Executor.__get_driver(coro)
            if driver is not None:
                sche_group = f"{driver.agent_name}.{sche_group}"

        if sche_group not in self.__coros:
            self.__coros[sche_group] = []

        if priority is not None:
            assert 0 <= priority <= 99, "Priority should be between 0 and 99"

            driver = Executor.__get_driver(coro)
            coro_name = coro.__name__
            assert (
                driver is not None
            ), f"{coro_name} is not a driver function, cannot set priority"

        if sche_order is not None:
            if sche_order == "parallel":
                sche_order = "model_first"
            driver = Executor.__get_driver(coro)
            coro_name = coro.__name__
            assert (
                driver is not None
            ), f"{coro_name} is not a driver function, cannot set sche_order"

        self.__coros[sche_group].append((coro, sche_order, priority))

    @staticmethod
    async def __sequential_execution_all(*tasks, complete_event=None):
        """
        Sequentially execute all tasks.

        Args:
            tasks: The tasks to be executed.
            complete_event: The event to set when all tasks are completed.
        """

        results = []
        for coro, sche_order, priority in tasks:
            driver = Executor.__get_driver(coro)

            if driver is not None:
                driver.priority = None
                driver.sche_order = None

                if priority is not None:
                    driver.priority = priority

                if sche_order is not None:
                    driver.sche_order = sche_order

            results.append(await coro)

        if complete_event is not None and not complete_event.is_set():
            complete_event.set()

        return results

    @staticmethod
    def __get_driver(coro):
        """
        Get the driver object of the coroutine.

        Args:
            coro: The coroutine object.
        """

        agent = coro.cr_frame.f_locals.get("agent", None)
        driver_name = coro.__name__

        if agent is not None:
            return agent.drivers.get(driver_name, None)
