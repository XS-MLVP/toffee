import asyncio

import toffee_test

import toffee
from toffee.agent import *
from toffee.env import *
from toffee.model import *

"""
Case 1
"""


class DUT:
    def __init__(self):
        self.event = asyncio.Event()

    def Step(self, cycles): ...


class MyAgent(Agent):
    def __init__(self):
        super().__init__(toffee.Bundle())

    @driver_method()
    async def driver1(): ...

    @driver_method()
    async def driver2(): ...

    @driver_method()
    async def driver3(): ...

    @driver_method()
    async def driver4(): ...

    @driver_method()
    async def driver5(): ...

    @driver_method()
    async def driver6(): ...

    @driver_method()
    async def driver7(): ...

    @driver_method()
    async def driver8(): ...


class MyModel(Model):
    def __init__(self):
        super().__init__()

        self.driver6 = DriverPort(agent_name="my_agent")
        self.my_driver7 = DriverPort("my_agent.driver7")
        self.my_agent__driver8 = DriverPort()

    @driver_hook(agent_name="my_agent")
    def driver1(): ...

    @driver_hook(agent_name="my_agent")
    def driver2(): ...

    @driver_hook(agent_name="my_agent", driver_name="driver3")
    def my_driver3(): ...

    @driver_hook("my_agent.driver4")
    def my_driver4(): ...

    @driver_hook()
    def my_agent__driver5(): ...


class MyEnv(Env):
    def __init__(self):
        super().__init__()
        self.my_agent = MyAgent()


def test_env1():
    async def my_test():
        env = MyEnv()
        env.attach(MyModel())

    toffee.run(my_test)


"""
Case 2
"""


class MyAgent2(Agent):
    def __init__(self):
        super().__init__(toffee.Bundle())

    @driver_method()
    async def driver1(): ...

    @driver_method()
    async def driver2(): ...


class MyModel2(Model):
    @agent_hook("my_agent")
    def my_agent_mark(): ...

    @driver_hook(agent_name="my_agent")
    def driver1(): ...

    @driver_hook(agent_name="my_agent")
    def driver2(): ...


class MyEnv2(Env):
    def __init__(self):
        super().__init__()
        self.my_agent = MyAgent2()


def test_env2():
    async def my_test():
        env = MyEnv2()
        env.attach(MyModel2())

    toffee.run(my_test)


"""
Case 3
"""


class MyModel3(Model):
    def __init__(self):
        super().__init__()

        self.my_agent = AgentPort()


def test_env3():
    async def my_test():
        env = MyEnv2()
        env.attach(MyModel3())

    toffee.run(my_test)


"""
Case 4
"""


class MyModel4(Model):
    def __init__(self):
        super().__init__()

        self.my_agent__driver1 = DriverPort()
        self.my_agent__driver2 = DriverPort()


def test_env4():
    async def my_test():
        env = MyEnv2()
        env.attach(MyModel4())

    toffee.run(my_test)


"""
Case 5
"""


class MyAgent5(Agent):
    def __init__(self):
        super().__init__(lambda: asyncio.Event().wait())

    @driver_method()
    async def driver1(self): ...

    @monitor_method()
    async def monitor1(self): ...

    @monitor_method()
    async def monitor2(self): ...

    @monitor_method()
    async def monitor3(self): ...

    @monitor_method()
    async def monitor4(self): ...


class MyModel5(Model):
    def __init__(self):
        super().__init__()

        self.my_agent__monitor1 = MonitorPort()
        self.monitor2 = MonitorPort(agent_name="my_agent")
        self.monitor3_mark = MonitorPort("my_agent.monitor3")
        self.monitor4_mark = MonitorPort(agent_name="my_agent", monitor_name="monitor4")

    @agent_hook("my_agent")
    def my_agent_mark(self): ...

    @driver_hook(agent_name="my_agent")
    def driver1(self): ...


class MyEnv5(Env):
    def __init__(self):
        super().__init__()
        self.my_agent = MyAgent5()


def test_env5():
    async def my_test():
        env = MyEnv5()
        env.attach(MyModel5())

    toffee.run(my_test)


"""
Case 6
"""


class MyAgent6(Agent):
    def __init__(self):
        super().__init__(toffee.Bundle())

    @driver_method()
    async def driver1(self, a, b, c=5): ...

    @driver_method()
    async def driver2(self): ...


class MyModel6(Model):
    def __init__(self):
        super().__init__()

        self.my_agent = AgentPort()

    async def main(self):
        req = await self.my_agent()
        assert req == ("driver1", {"a": 1, "b": 2, "c": 5})


class MyEnv6(Env):
    def __init__(self):
        super().__init__()
        self.my_agent = MyAgent6()


def test_env6():
    async def my_test():
        dut = DUT()
        toffee.start_clock(dut)

        env = MyEnv6()
        env.attach(MyModel6())

        await env.my_agent.driver1(1, b=2)

    toffee.run(my_test)
