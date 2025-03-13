import toffee
from toffee import *
import toffee_test

from base import AdderBundle, Adder


class AdderAgent(Agent):
    @driver_method()
    async def add(self, a, b):
        self.bundle.a.value = a
        self.bundle.b.value = b
        await self.bundle.step()
        return self.bundle.sum.value

    @monitor_method()
    async def monitor1(self):
        return self.bundle.as_dict()


class TestModelHooks:
    def test_driverhook_scheorder(self):
        results = []

        class AdderAgent(Agent):
            @driver_method()
            async def add(self, a, b):
                results.append(("agent", a, b))
                self.bundle.a.value = a
                self.bundle.b.value = b
                await self.bundle.step()
                return self.bundle.sum.value
        class AdderEnv(Env):
            def __init__(self, dut):
                super().__init__()
                bundle = AdderBundle.from_prefix("io_").bind(dut)
                self.adder_agent = AdderAgent(bundle)
        class AdderModel(Model):
            @driver_hook(agent_name="adder_agent", sche_order="model_first", priority=2)
            def add(self, a, b):
                results.append(("model_before", a, b))
                return a + b
        class AdderModel2(Model):
            @driver_hook(agent_name="adder_agent", sche_order="dut_first", priority=1)
            def add(self, a, b):
                results.append(("model_after", a, b))
                return a + b
        def env_handle():
            dut = Adder()
            start_clock(dut)
            return AdderEnv(dut).attach(AdderModel()).attach(AdderModel2())

        async def test(env):
            for i in range(4):
                await env.adder_agent.add(i, i)

        toffee.run(test, env_handle)

        assert results == [('model_before', 0, 0), ('agent', 0, 0), ('model_after', 0, 0),
                            ('model_before', 1, 1), ('agent', 1, 1), ('model_after', 1, 1),
                            ('model_before', 2, 2), ('agent', 2, 2), ('model_after', 2, 2),
                            ('model_before', 3, 3), ('agent', 3, 3), ('model_after', 3, 3)]

    def test_driverhook_and_monitorhook_priority(self):
        results = []

        class MyAdderAgent(AdderAgent):
            @driver_method()
            async def add(self, a, b):
                results.append(("driver", a, b))
                self.bundle.a.value = a
                self.bundle.b.value = b
                await self.bundle.step()
                return self.bundle.sum.value
            @monitor_method()
            async def monitor1(self):
                results.append(("monitor1", self.bundle.a.value, self.bundle.b.value))
                return self.bundle.as_dict()
            @monitor_method()
            async def monitor2(self):
                results.append(("monitor2", self.bundle.a.value, self.bundle.b.value))
                return self.bundle.as_dict()
        class AdderEnv(Env):
            def __init__(self, dut):
                super().__init__()
                bundle = AdderBundle.from_prefix("io_").bind(dut)
                self.adder_agent = MyAdderAgent(bundle)
        class AdderModel(Model):
            @driver_hook(agent_name="adder_agent", priority=2)
            def add(self, a, b):
                results.append(("model driver2", a, b))
                return a + b
            @monitor_hook(agent_name="adder_agent", priority=1)
            def monitor1(self, item):
                results.append(("model monitor1", item["a"], item["b"]))
            @monitor_hook(agent_name="adder_agent", priority=3)
            def monitor2(self, item):
                results.append(("model monitor3", item["a"], item["b"]))

        def env_handle():
            dut = Adder()
            start_clock(dut)
            return AdderEnv(dut).attach(AdderModel())

        async def test(env):
            for i in range(4):
                await env.adder_agent.add(i, i)
        toffee.run(test, env_handle)

        assert results == [('model driver2', 0, 0), ('driver', 0, 0),
                           ('monitor1', 0, 0), ('monitor2', 0, 0), ('model monitor1', 0, 0), ('model driver2', 1, 1), ('model monitor3', 0, 0), ('driver', 1, 1),
                           ('monitor1', 1, 1), ('monitor2', 1, 1), ('model monitor1', 1, 1), ('model driver2', 2, 2), ('model monitor3', 1, 1), ('driver', 2, 2),
                           ('monitor1', 2, 2), ('monitor2', 2, 2), ('model monitor1', 2, 2), ('model driver2', 3, 3), ('model monitor3', 2, 2), ('driver', 3, 3),
                           ('monitor1', 3, 3), ('monitor2', 3, 3), ('model monitor1', 3, 3), ('model monitor3', 3, 3)]

    def test_agenthook(self):
        results = []

        class MyAdderAgent(Agent):
            @driver_method()
            async def add(self, a, b):
                results.append(("driver", a, b))
                self.bundle.a.value = a
                self.bundle.b.value = b
                await self.bundle.step()
                return self.bundle.sum.value

            @monitor_method()
            async def monitor(self):
                results.append(("monitor", self.bundle.a.value, self.bundle.b.value))
                return self.bundle.as_dict()

        class AdderEnv(Env):
            def __init__(self, dut):
                super().__init__()
                bundle = AdderBundle.from_prefix("io_").bind(dut)
                self.adder_agent = MyAdderAgent(bundle)
        class AdderModel(Model):
            @agent_hook("adder_agent", sche_order="model_first", priority=3)
            def agent1(self, name, args):
                results.append(("model agent1", name, args["a"], args["b"]))
                if name == "add":
                    return args["a"] + args["b"]
            @agent_hook(methods=["adder_agent.add"], sche_order="dut_first", priority=0)
            def agent2(self, name, args):
                results.append(("model agent2", name,  args["a"], args["b"]))
                return args['a'] + args['b']
            @driver_hook(agent_name="adder_agent", priority=1)
            def add(self, a, b):
                results.append(("model driver", a, b))
                return a + b
            @monitor_hook(agent_name="adder_agent", priority=2)
            def monitor(self, item):
                results.append(("model monitor", item["a"], item["b"]))

        def env_handle():
            dut = Adder()
            start_clock(dut)
            return AdderEnv(dut).attach(AdderModel())

        async def test(env):
            for i in range(4):
                await env.adder_agent.add(i, i)
        toffee.run(test, env_handle)

        assert results == [('model driver', 0, 0), ('model agent1', 'add', 0, 0), ('driver', 0, 0),
                           ('monitor', 0, 0), ('model agent2', 'add', 0, 0), ('model driver', 1, 1), ('model monitor', 0, 0), ('model agent1', 'monitor', 0, 0), ('model agent1', 'add', 1, 1), ('driver', 1, 1),
                           ('monitor', 1, 1), ('model agent2', 'add', 1, 1), ('model driver', 2, 2), ('model monitor', 1, 1), ('model agent1', 'monitor', 1, 1), ('model agent1', 'add', 2, 2), ('driver', 2, 2),
                           ('monitor', 2, 2), ('model agent2', 'add', 2, 2), ('model driver', 3, 3), ('model monitor', 2, 2), ('model agent1', 'monitor', 2, 2), ('model agent1', 'add', 3, 3), ('driver', 3, 3),
                           ('monitor', 3, 3), ('model agent2', 'add', 3, 3), ('model monitor', 3, 3), ('model agent1', 'monitor', 3, 3)]










