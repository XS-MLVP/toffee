from toffee import *


class AdderModelWithDriverHook(Model):
    @driver_hook(agent_name="add_agent")
    def exec_add(self, a, b, cin):
        result = a + b + cin
        sum = result & ((1 << 64) - 1)
        cout = result >> 64
        return sum, cout

    @agent_hook(agent_name="add_agent")
    def agent_add(self, name, item):
        print(name, item)


class AdderModelWithMonitorHook(Model):
    @monitor_hook(agent_name="add_agent")
    def monitor_once(self, item):
        sum = item["a"] + item["b"] + item["cin"]
        assert sum & ((1 << 64) - 1) == item["sum"]
        assert sum >> 64 == item["cout"]


class AdderModelWithPort(Model):
    def __init__(self):
        super().__init__()

        self.exec_add_port = DriverPort("add_agent.exec_add")
        self.monitor_once_port = MonitorPort("add_agent.monitor_once")

    async def main(self):
        while True:
            oprands = await self.exec_add_port()
            result = await self.monitor_once_port()
            sum = oprands["a"] + oprands["b"] + oprands["cin"]

            assert {
                "a": oprands["a"],
                "b": oprands["b"],
                "cin": oprands["cin"],
                "sum": sum & ((1 << 64) - 1),
                "cout": sum >> 64,
            } == result
