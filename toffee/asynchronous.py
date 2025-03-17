__all__ = [
    "Event",
    "Queue",
    "sleep",
    "create_task",
    "run",
    "gather",
    "start_clock",
    "main_coro",
]

import asyncio
import inspect
import sys

from .bundle import Bundle
from .logger import summary

"""Asynchronous event definition

This section implements specific asynchronous event requirements to meet the clock requirements in the circuit.
Specifically, we need to complete all executable tasks before the next clock event arrives.
"""

callback_list = []


def add_callback(coro, *args, **kwargs):
    """
    Add a callback function to the callback list.
    """

    callback_list.append((coro, args, kwargs))


async def __execute_callback():
    """
    Execute the callback function. The Callback will be executed between the next clock time after other_task_done.
    """

    need_rerun = False
    for func, args, kwargs in callback_list:
        need_rerun |= await func(*args, **kwargs)
    return need_rerun


def task_run():
    """
    Set the flag to indicate that a new task has been run.
    """

    asyncio.get_event_loop().new_task_run = True


def __has_unwait_task():
    """
    Detects whether a task exists, is not waiting, or is waiting for an event that has already been triggered.
    """

    for task in asyncio.all_tasks():

        if task.get_name() == "__clock_loop":
            continue

        if task._fut_waiter is None or task._fut_waiter._state == "FINISHED":
            return True

    return False


async def __run_once():
    """
    The event loop executes one round.
    """

    asyncio.get_event_loop().new_task_run = False

    await asyncio.sleep(0)


async def __other_tasks_done():
    """
    Wait for all tasks to complete. This means that all tasks are waiting at this time, and there are no tasks that
    can be executed.
    """

    await __run_once()
    while __has_unwait_task() or asyncio.get_event_loop().new_task_run:
        await __run_once()


async def cancel_all_tasks():
    tasks = {
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.get_name() == "main_coro"
    }
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def execute_all_coros():
    while True:
        await __other_tasks_done()
        if not (await __execute_callback()):
            break

    if asyncio.get_event_loop().test_done:
        await cancel_all_tasks()
        await asyncio.sleep(0)
        return


class Event(asyncio.Event):
    """
    Change the function in the Event to meet the asynchronous requirements.
    """

    def __init__(self):
        super().__init__()

    async def wait(self):
        await super().wait()
        task_run()


class Queue(asyncio.Queue):
    """
    Change the function in the Queue to meet the asynchronous requirements.
    """

    def __init__(self):
        super().__init__()

    async def put(self, item):
        await super().put(item)
        task_run()

    async def get(self):
        ret = await super().get()
        task_run()
        return ret


async def sleep(delay: float):
    """
    Change the implementation of the sleep function to meet the asynchronous requirements.
    """

    await asyncio.sleep(delay)
    task_run()


"""Asynchronous primary interface

Using the asynchronous event logic defined above, the external asynchronous interface in toffee library is implemented.
"""


async def __clock_loop(dut):
    """
    The clock loop function, which is the main loop of the asynchronous event.
    """

    while True:
        await execute_all_coros()
        dut.Step(1)
        dut.event.set()
        dut.event.clear()


create_task = asyncio.create_task


def start_clock(dut):
    """
    Start a clock loop on a DUT.
    """
    # When start_clock is called, global_clock_event points to the clock event in the dut
    loop = asyncio.get_event_loop()
    loop.global_clock_event = dut.event

    task = create_task(__clock_loop(dut))
    task.set_name("__clock_loop")


def set_clock_event(dut, loop):
    """
    Set the clock event for the DUT.

    In earlier versions of python, the original Event definition cannot be used in the new event loop.
    """

    new_event = asyncio.Event(loop=loop)
    dut.xclock._step_event = new_event
    dut.event = new_event

    for xpin_info in Bundle.dut_all_signals(dut):
        xpin = xpin_info["signal"]
        xpin.event = new_event


def handle_exception(loop, context):
    """
    Handle exceptions in the event loop.
    """

    loop.default_exception_handler(context)
    loop.stop()


async def main_coro(test, env_handle=None):
    """
    Wrapper for a coroutine to meet the rules for toffee.

    Args:
        test: The test to be run, it can be a coroutine or a function.
        env_handle: A handle to create the environment. make sure the env is created before the test starts. if the
                env_handle is provided, the test will be called with the env_handle's return value. when the env_handle
                is set, make sure the test is a function and has the same number of arguments as the env_handle's
                return.

    Returns:
        The result of the coroutine (or function).
    """

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)
    loop.new_task_run = False
    loop.test_done = False
    loop.delayer_list = []

    asyncio.current_task().set_name("main_coro")

    if env_handle:
        args = env_handle()
        if not isinstance(args, tuple):
            args = (args,)
        ret = await create_task(test(*args))
    else:
        if inspect.iscoroutine(test):
            ret = await test
        else:
            ret = await test()

    loop.test_done = True

    # Wait for the last clock event to complete all outstanding tasks during the period
    loop = asyncio.get_event_loop()
    if hasattr(loop, "global_clock_event"):
        await loop.global_clock_event.wait()

    summary()

    return ret


def run(test, env_handle=None, dut=None):
    """
    Start the asynchronous event loop and run the coroutine.

    Args:
        test: The test to be run, it can be a coroutine or a function.
        env_handle: A handle to create the environment. make sure the env is created before the test starts. if the
                env_handle is provided, the test will be called with the env_handle's return value. when the env_handle
                is set, make sure the test is a function and has the same number of arguments as the env_handle's
                return.
        dut: The DUT object.

    Returns:
        The result of the coroutine (or function).
    """

    coro = main_coro(test, env_handle)

    if sys.version_info >= (3, 10, 1):
        return asyncio.run(coro)

    assert (
        dut is not None
    ), "Your current version of python is less than 3.10.1, need to provide the dut parameter"

    loop = asyncio.get_event_loop()
    set_clock_event(dut, loop)
    result = loop.run_until_complete(coro)
    return result


async def gather(*coros):
    """
    Gather multiple coroutines and run them at the same time.
    """

    all_tasks = []
    for coro in coros:
        all_tasks.append(create_task(coro))

    results = []
    for task in all_tasks:
        results.append(await task)

    return results


"""
Component definition
"""

from ._base import MObject


class Component(MObject):
    """
    A Component is a component that has its own execution flow.
    """

    def __init__(self):
        create_task(self.__main_wrapper())

    async def __main_wrapper(self):
        """Make sure the main function is executed after test start"""
        while True:
            loop = asyncio.get_event_loop()
            if hasattr(loop, "global_clock_event"):
                await loop.global_clock_event.wait()
                break
            await asyncio.sleep(0)

        await self.main()

    async def main(self):
        raise NotImplementedError("main function not implemented")
