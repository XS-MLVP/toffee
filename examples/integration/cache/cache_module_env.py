"""Loading and settled input drives for separately exported Cache modules."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

from toffee import FallingEdge, RisingEdge, as_xdata


_native_runtime = None


def load_module(name):
    global _native_runtime
    variable = "CACHE_" + name.removeprefix("Cache").upper() + "_DUT_DIR"
    root = Path(os.environ.get(variable, Path(os.environ.get("CACHE_MODULE_DIR", "output/cache-modules")) / name)).resolve()
    module_id = "_picker_cache_module_" + str(abs(hash(str(root))))
    if module_id not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_id, root / "__init__.py", submodule_search_locations=[str(root)])
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load Cache module {root}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_id] = module
        # SWIG's XSignalCFG factories use process-wide registered Python
        # classes. Separate copies of pyxspcomm can therefore return an XData
        # from another package, which generated GetInternalSignal mistakes for
        # an array. Use the full Cache's runtime when it is already imported,
        # or the first module's runtime for a modules-only run.
        if _native_runtime is None:
            for imported in tuple(sys.modules.values()):
                if (getattr(imported, "DUTCacheSignalCFG", None) is not None
                        and getattr(imported, "xsp", None) is not None):
                    _native_runtime = imported.xsp
                    break
        if _native_runtime is not None:
            sys.modules[module_id + ".xspcomm"] = _native_runtime
        spec.loader.exec_module(module)
        _native_runtime = module.xsp
    return getattr(sys.modules[module_id], "DUT" + name)


def initialize_module(dut):
    for signal in vars(dut).values():
        data = as_xdata(signal)
        if hasattr(data, "IsInIO") and data.IsInIO():
            data.AsImmWrite()
            data.Set(0)


class ModuleCycles:
    def __init__(self, dut):
        self.dut = dut

    def drive(self, **values):
        for name, value in values.items():
            getattr(self.dut, name).Set(value)

    async def advance(self, **values):
        self.drive(**values)
        self.dut.RefreshComb()
        await RisingEdge(self.dut.clock)
        await FallingEdge(self.dut.clock)
