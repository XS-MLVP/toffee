from base import FDUT
import toffee
from toffee import *

class MyAgent(Agent):
    def __init__(self, dut, infos):
        self.infos = infos
        self.dut = dut
        super().__init__(Bundle())
    @driver_method()
    async def driver1(self):
        self.infos.append("driver1")
        await self.dut.event.wait()
        await self.dut.event.wait()
    @driver_method()
    async def driver2(self):
        self.infos.append("driver2")
        await self.dut.event.wait()
        await self.dut.event.wait()

def test_executor():
    class MyModel(Model):
        def __init__(self, infos):
            super().__init__()
            self.infos = infos
        @driver_hook(agent_name="my_agent")
        def driver1(self):
            self.infos.append("model1")
        @driver_hook(agent_name="my_agent")
        def driver2(self):
            self.infos.append("model2")
    class MyEnv(Env):
        def __init__(self, dut, infos):
            super().__init__()
            self.my_agent = MyAgent(dut, infos)

    async def my_test():
        dut = FDUT()
        toffee.start_clock(dut)

        infos = []

        env = MyEnv(dut, infos)
        env.attach(MyModel(infos))

        async with Executor() as exec:
            exec(env.my_agent.driver1(), sche_order="model_first")
        await ClockCycles(dut)
        assert infos == ["model1", "driver1"]
        infos.clear()

        async with Executor() as exec:
            exec(env.my_agent.driver1(), sche_order="dut_first")
        await ClockCycles(dut)
        assert infos == ["driver1", "model1"]
        infos.clear()

        async with Executor() as exec:
            exec(env.my_agent.driver1(), sche_order="dut_first")
        await ClockCycles(dut)
        assert infos == ["driver1", "model1"]
        infos.clear()

        async with Executor() as exec:
            exec(env.my_agent.driver1(), sche_order="dut_first")
            exec(env.my_agent.driver2(), sche_order="dut_first")
        await ClockCycles(dut)
        assert infos == ["driver1", "driver2", "model1", "model2"]
        infos.clear()

        async with Executor() as exec:
            exec(env.my_agent.driver1(), sche_order="dut_first")
            exec(env.my_agent.driver2(), sche_order="dut_first", priority=1)
        await ClockCycles(dut)
        assert infos == ["driver1", "driver2", "model2", "model1"]
        infos.clear()

        async with Executor() as exec:
            exec(env.my_agent.driver1(), sche_order="model_first", priority=3)
            exec(env.my_agent.driver2(), sche_order="model_first", priority=2)
        await ClockCycles(dut)
        assert infos == ["model2", "model1", "driver2", "driver1"]
        infos.clear()

    toffee.run(my_test)


def test_executor_sche_group():
    class MyEnv2(Env):
        def __init__(self, dut):
            super().__init__()
            self.my_agent = MyAgent(dut, [])
            self.my_agent2 = MyAgent(dut, [])
    class MyModel2(Model):
        @agent_hook()
        def my_agent(self, name, args): ...
        @agent_hook()
        def my_agent2(self, name, args): ...

    async def my_test():
        dut = FDUT()
        toffee.start_clock(dut)

        env = MyEnv2(dut)
        env.attach(MyModel2())

        async with Executor() as exec:
            exec(env.my_agent.driver1())
            exec(env.my_agent2.driver1())

        assert len(exec.get_results()) == 2

    toffee.run(my_test)
