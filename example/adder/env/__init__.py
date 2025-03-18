from .agent import AdderAgent
from .agent import AdderBundle
from .refmodel import AdderModelWithDriverHook
from .refmodel import AdderModelWithMonitorHook
from .refmodel import AdderModelWithPort
from toffee import *


class AdderEnv(Env):
    def __init__(self, adder_bundle):
        super().__init__()
        self.add_agent = AdderAgent(adder_bundle)

        self.attach(AdderModelWithDriverHook())
        # self.attach(AdderModelWithMonitorHook())
        # self.attach(AdderModelWithPort())
