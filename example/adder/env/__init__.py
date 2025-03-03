from toffee import *
from .agent import AdderAgent, AdderBundle
from .refmodel import AdderModelWithDriverHook, AdderModelWithMonitorHook, AdderModelWithPort

class AdderEnv(Env):
    def __init__(self, adder_bundle):
        super().__init__()
        self.add_agent = AdderAgent(adder_bundle)

        self.attach(AdderModelWithDriverHook())
        self.attach(AdderModelWithMonitorHook())
        self.attach(AdderModelWithPort())
