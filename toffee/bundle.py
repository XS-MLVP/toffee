__all__ = [
    "Bundle",
    "WriteMode",
    "DummySignal",
    "Signal",
    "Signals",
    "SignalList",
    "BundleList",
]

import random
import re
from enum import Enum
from queue import Queue
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ._base import MObject
from .logger import *


class DummySignal:
    """
    A dummy signal class that does nothing. It will return None when accessed,
    and do nothing when set.
    """

    def __setattr__(self, __name, __value):
        pass

    def __getattribute__(self, __name):
        return None


class WriteMode(Enum):
    """
    The write mode of a bundle.
    """

    Imme = 0
    Rise = 1
    Fall = 2


class UnconnectedSignal:
    def __getattribute__(self, name: str):
        raise AttributeError(f"Can't access unconnected signal")

    def __setattr__(self, name: str, value):
        raise AttributeError(f"Can't access unconnected signal")

    def __str__(self):
        return "UnconnectedSignal()"


class Signal(UnconnectedSignal): ...


def Signals(num: int):
    if num == 1:
        return Signal()
    return [Signal() for _ in range(num)]


class SignalList:
    def __init__(self, format: str, limit: int, rule=None):
        assert limit > 0, "limit must be greater than 0"
        assert "#" in format, "format must contain at least one #"

        self.format = format
        self.signals = [Signal() for _ in range(limit)]
        self.names = []
        if rule is None:
            rule = lambda num: str(num)
        for i in range(limit):
            self.names.append(format.replace("#", rule(i)))

    def bind_signal(
        self, bundle, signal_name: str, signal, info_bundle_name, info_dut_name
    ):
        assert (
            signal_name in self.names
        ), f"signal name {signal_name} not in signal list"

        index = self.names.index(signal_name)
        self.signals[index] = signal
        bundle._Bundle__update_signal_info(signal)
        info(
            f'dut\'s signal "{info_dut_name}" is connected to "{info_bundle_name}[{index}]"'
        )

    def assign(self, value):
        assert len(value) == len(
            self.signals
        ), "value length must match signal list length"
        for i, signal in enumerate(self.signals):
            signal.value = value[i]

    def __getitem__(self, key):
        return self.signals[key]

    def __str__(self):
        signal_str = ", ".join(
            f"{idx}: {signal}" for idx, signal in enumerate(self.signals)
        )
        return f"SignalList({signal_str})"

    @classmethod
    def from_signallist(cls, old_signal_list: "SignalList"):
        signal_list = cls("#", 1)
        signal_list.format = old_signal_list.format
        signal_list.signals = old_signal_list.signals[:]
        signal_list.names = old_signal_list.names[:]
        return signal_list


class BundleList:
    def __init__(self, bundle, format: str, limit: int, rule=None):
        assert limit > 0, "limit must be greater than 0"
        assert "#" in format, "format must contain at least one #"

        self.format = format
        self.names = []
        self.bundles = []

        if rule is None:
            rule = lambda num: str(num)
        for i in range(limit):
            name = format.replace("#", rule(i))
            self.names.append(name)
            self.bundles.append(bundle.from_prefix(name))

    def assign(self, value, multilevel=True):
        for i, bundle in enumerate(self.bundles):
            bundle.assign(value[i], multilevel)

    def __getitem__(self, key):
        return self.bundles[key]

    def __str__(self):
        bundle_str = ", ".join(
            f"{idx}: {bundle}" for idx, bundle in enumerate(self.bundles)
        )
        return f"BundleList({bundle_str})"

    @classmethod
    def from_bundlelist(cls, old_bundle_list: "BundleList"):
        bundle_list = cls(old_bundle_list.bundles[0], "#", 1)
        bundle_list.format = old_bundle_list.format
        bundle_list.names = old_bundle_list.names[:]
        bundle_list.bundles = old_bundle_list.bundles[:]
        return bundle_list


class BindMethod(MObject):
    """
    A bind method is a way to connect signals to a bundle.
    """

    def __init__(self, method, value):
        self.method = method
        self.method_value = value

    def bind(self, bundle, all_signals, level_string, detection_mode):
        """
        Bind the signals to the bundle.

        Args:
            bundle: The bundle to bind the signals to.
            all_signals: A list of signals to bind.
            level_string: The string of the current level.
            detection_mode: Whether the method is in detection mode. if it is, the bundle
                            will not be connected to the signals.

        Returns:
            A tuple of three lists:
            - A list of connected signals.
            - A list of matching signals that are not connected.  The item's name in this
              list is changed.
            - A list of signals that are not matched.
        """

        raise NotImplementedError

    @staticmethod
    def get_matching_signal_list(bundle, signal_name: str) -> SignalList:
        """
        Get the matching signal list in the bundle.

        Args:
            bundle: The bundle to get the matching signal list from.
            signal_name: The name of the signal to get.

        Returns:
            (signal list name, signal list) if the signal is found in the bundle, None otherwise.
        """

        for signal_list_name, signal_list in bundle._Bundle__all_signal_lists():
            if signal_name in signal_list.names:
                return (signal_list_name, signal_list)
        return None


class PrefixBindMethod(BindMethod):
    """
    A bind method that connects signals to a bundle by matching the prefix of the signal name.
    """

    def __init__(self, prefix):
        super().__init__("prefix", prefix)

    def bind(self, bundle, all_signals, level_string, detection_mode):
        connected_signals = []  # Matched and connected signals
        matching_signals = []  # Matched but not connected signals,
        # item's name in the list is the name without prefix
        remain_signals = []  # Not matched signals

        prefix = self.method_value
        for signal in all_signals:
            if signal["name"].startswith(prefix):
                name_no_prefix = signal["name"][len(prefix) :]

                if name_no_prefix in bundle.current_level_signals:
                    if not detection_mode:
                        bundle._Bundle__add_signal_attr(
                            name_no_prefix,
                            signal["signal"],
                            info_dut_name=signal["org_name"],
                            info_bundle_name=Bundle._Bundle__appended_level_string(
                                level_string, name_no_prefix
                            ),
                        )
                        connected_signals.append(name_no_prefix)
                elif signal_list_name_and_inst := self.get_matching_signal_list(
                    bundle, name_no_prefix
                ):
                    if not detection_mode:
                        signal_list_name_and_inst[1].bind_signal(
                            bundle,
                            name_no_prefix,
                            signal["signal"],
                            info_dut_name=signal["org_name"],
                            info_bundle_name=Bundle._Bundle__appended_level_string(
                                level_string, signal_list_name_and_inst[0]
                            ),
                        )
                        connected_signals.append(name_no_prefix)
                else:
                    matching_signals.append(
                        {
                            "name": signal["name"][len(prefix) :],
                            "org_name": signal["org_name"],
                            "signal": signal["signal"],
                        }
                    )
            else:
                remain_signals.append(signal)

        return (connected_signals, matching_signals, remain_signals)


class RegexBindMethod(BindMethod):
    """
    A bind method that connects signals to a bundle by matching the regex of the signal name.
    """

    def __init__(self, regex):
        super().__init__("regex", regex)

    def bind(self, bundle, all_signals, level_string, detection_mode):
        connected_signals = []  # Matched and connected signals
        matching_signals = []  # Matched but not connected signals,
        # item's name in the list is the name in the captured group
        remain_signals = []  # Not matched signals

        regex = self.method_value
        for signal in all_signals:
            match = re.search(regex, signal["name"])
            if match is not None:
                groups = ["" if x is None else x for x in match.groups()]
                name = "".join(groups)
                if name in bundle.current_level_signals:
                    if not detection_mode:
                        bundle._Bundle__add_signal_attr(
                            name,
                            signal["signal"],
                            info_dut_name=signal["org_name"],
                            info_bundle_name=Bundle._Bundle__appended_level_string(
                                level_string, name
                            ),
                        )
                    connected_signals.append(name)
                elif signal_list_name_and_inst := self.get_matching_signal_list(
                    bundle, name
                ):
                    if not detection_mode:
                        signal_list_name_and_inst[1].bind_signal(
                            bundle,
                            name,
                            signal["signal"],
                            info_dut_name=signal["org_name"],
                            info_bundle_name=Bundle._Bundle__appended_level_string(
                                level_string, signal_list_name_and_inst[0]
                            ),
                        )
                    connected_signals.append(name)
                else:
                    matching_signals.append(
                        {
                            "name": name,
                            "org_name": signal["org_name"],
                            "signal": signal["signal"],
                        }
                    )
            else:
                remain_signals.append(signal)

        return (connected_signals, matching_signals, remain_signals)


class DictBindMethod(BindMethod):
    """
    A bind method that connects signals to a bundle by matching the dictionary.
    """

    def __init__(self, dict):
        super().__init__("dict", dict)

    def bind(self, bundle, all_signals, level_string, detection_mode):
        connected_signals = []  # Matched and connected signals
        matching_signals = []  # Matched but not connected signals,
        # item's name in the list is the name without prefix
        remain_signals = []  # Not matched signals

        dict = self.method_value
        for signal in all_signals:
            if signal["name"] in dict.values():
                name = list(dict.keys())[list(dict.values()).index(signal["name"])]

                if name in bundle.current_level_signals:
                    if not detection_mode:
                        bundle._Bundle__add_signal_attr(
                            name,
                            signal["signal"],
                            info_dut_name=signal["org_name"],
                            info_bundle_name=Bundle._Bundle__appended_level_string(
                                level_string, name
                            ),
                        )
                    connected_signals.append(name)
                elif signal_list_name_and_inst := self.get_matching_signal_list(
                    bundle, name
                ):
                    if not detection_mode:
                        signal_list_name_and_inst[1].bind_signal(
                            bundle,
                            name,
                            signal["signal"],
                            info_dut_name=signal["org_name"],
                            info_bundle_name=Bundle._Bundle__appended_level_string(
                                level_string, signal_list_name_and_inst[0]
                            ),
                        )
                    connected_signals.append(name)
                else:
                    matching_signals.append(
                        {
                            "name": name,
                            "org_name": signal["org_name"],
                            "signal": signal["signal"],
                        }
                    )
            else:
                remain_signals.append(signal)

        return (connected_signals, matching_signals, remain_signals)


class Bundle(MObject):
    """
    A bundle is a collection of signals in a DUT.
    """

    signals = []

    def __init__(self):
        """
        Create a bundle.
        Instances created using this method are matched directly in bind. The create
        instance method provided by from_dict, from_prefix and from_regex enable easier connections.
        """

        self.name = ""  # The name of the bundle
        self.bound = False  # Whether the bundle is bound to a DUT
        self.write_mode = None  # The write mode of the bundle
        self.current_level_signals = []  # The signal names in the current level

        self.__clock_event = None
        self.__connect_method = PrefixBindMethod("")
        self.__dut_requests__ = Queue()
        self.__dut_instance__ = None
        self.__blocked_request__ = None

        self.__set_current_level_signal()

        # Recreate subbundles and signal lists for each instance
        for sub_bundle_name, sub_bundle in self.__all_sub_bundles():
            new_sub_bundle = sub_bundle.__class__()
            new_sub_bundle.current_level_signals = sub_bundle.current_level_signals
            new_sub_bundle.__connect_method = sub_bundle.__connect_method
            setattr(self, sub_bundle_name, new_sub_bundle)

        # Recreate signal lists for each instance
        for signal_list_name, signal_list in self.__all_signal_lists():
            new_signal_list = SignalList.from_signallist(signal_list)
            setattr(self, signal_list_name, new_signal_list)

        # Recreate bundle lists for each instance
        for bundle_list_name, bundle_list in self.__all_bundle_lists():
            new_bundle_list = BundleList.from_bundlelist(bundle_list)
            setattr(self, bundle_list_name, new_bundle_list)

    def ___dut_call_on_rise__(self, cycle):
        """
        Call the on rise method of target DUT.

        Args:
            cycle: The cycle of the rise.
        """
        request = None
        if self.__blocked_request__ is not None:
            if self.__blocked_request__["__condition_func__"](
                cycle, self, self.__blocked_request__.get("__condition_args__", None)
            ):
                request = self.__blocked_request__
                request.pop("__condition_func__", None)
                request.pop("__condition_args__", None)
                self.__blocked_request__ = None
            else:
                return
        if request is None:
            if self.__dut_requests__.empty():
                return
            request = self.__dut_requests__.get()
            if request is None:
                return
        if callable(request):
            data = request(cycle, self)
            if data is None:
                return
            request = data
        # check condition
        if "__condition_func__" in request:
            if not request["__condition_func__"](
                cycle, self, request.get("__condition_args__", None)
            ):
                self.__blocked_request__ = request
                return
        request.pop("__condition_func__", None)
        request.pop("__condition_args__", None)
        # callbacks
        funcs = request.pop("__funcs__", None)
        return_bundles = request.pop("__return_bundles__", None)
        funcreturns = []
        if not isinstance(funcs, list):
            funcs = [funcs]
        self.assign(request)
        for func in funcs:
            if callable(func):
                funcreturns.append(func(cycle, self))
            else:
                funcreturns.append(None)
        if len(funcreturns) == 1:
            funcreturns = funcreturns[0]
        # return bundle
        if return_bundles is None:
            return
        if not isinstance(return_bundles, list):
            return_bundles = [return_bundles]
        ret_data = []
        for return_bundle in return_bundles:
            if isinstance(return_bundle, Bundle):
                ret_data.append(return_bundle.as_dict())
        if len(ret_data) == 1:
            ret_data = ret_data[0]
        # save return data in request
        request["__funcs_return__"] = funcreturns
        request["__return_values__"] = ret_data
        request["__return_cycles__"] = cycle

    def make_requset_response_for(self, dut):
        """
        Make a request response for the dut.

        Args:
            dut: The dut to make the request response for.
        """
        if self.__dut_instance__ is not None:
            error("The dut instance is already set")
        dut.StepRis(self.___dut_call_on_rise__)
        self.__dut_instance__ = dut

    def process_requests(self, request: Optional[Union[Dict, List[Dict]]]):
        """
        Process the requests.

        Args:
            request: The request to process.
        """
        for key in [
            "__condition_func__",
            "__condition_args__",
            "__funcs__",
            "__return_bundles__",
        ]:
            if hasattr(self, key):
                error(f"bundule can not with name: {key}")
        assert self.__dut_requests__.empty(), "The request queue is not empty"
        if self.__dut_instance__ is None:
            error(
                "The dut instance is not set, need to call make_requset_response_for first"
            )
        if not isinstance(request, list):
            request = [request]
        for req in request:
            self.__dut_requests__.put(req)
        while not self.__dut_requests__.empty():
            self.__dut_instance__.Step(1)
        ret = []
        for req in request:
            if isinstance(req, dict) and "__return_values__" in req:
                ret.append(
                    {
                        "data": req["__return_values__"],
                        "cycle": req["__return_cycles__"],
                    }
                )
                ret[-1]["__funcs_return__"] = req["__funcs_return__"]
        return ret

    def set_name(self, name):
        """
        Set the name of the bundle.

        Args:
            name: The name of the bundle.

        Returns:
            The bundle itself.
        """

        self.name = name
        return self

    def set_prefix(self, prefix=""):
        """
        Set the bundle to bind from prefix.

        Args:
            prefix: The prefix to match the signals.

        Returns:
            The bundle itself.
        """

        self.__connect_method = PrefixBindMethod(prefix)
        return self

    def set_regex(self, regex=r""):
        """
        Set the bundle to bind from regex.

        Args:
            regex: The regex to match the signals.

        Returns:
            The bundle itself.
        """

        self.__connect_method = RegexBindMethod(regex)
        return self

    def set_dict(self, dict={}):
        """
        Set the bundle to bind from a dictionary.

        Args:
            dict: The dictionary to guide bundle connections

        Returns:
            The bundle itself.
        """

        self.__connect_method = DictBindMethod(dict)
        self.__check_dict_value(dict)
        return self

    def set_write_mode(self, write_mode: WriteMode):
        """
        Set the write mode of the bundle. it will change the write mode of all signals
        in the bundle.

        Args:
            write_mode: The write mode to set.

        Returns:
            The bundle itself.
        """

        self.write_mode = write_mode
        self.__set_all_signals_write_mode(write_mode)

        return self

    def __set_all_signals_write_mode(self, write_mode: WriteMode):
        """
        Set the write mode of all signals in the bundle.

        Args:
            write_mode: The write mode to set.
        """

        for _, signal in self.all_signals():
            if Bundle.__is_instance_of_xpin(signal) and not signal.IsOutIO():
                if write_mode == WriteMode.Imme:
                    signal.AsImmWrite()
                elif write_mode == WriteMode.Rise:
                    signal.AsRiseWrite()
                elif write_mode == WriteMode.Fall:
                    signal.AsFallWrite()
                else:
                    raise ValueError("write mode must be Imme, Rise, or Fall")

    def set_write_mode_as_imme(self):
        """
        Set the write mode of the bundle to immediate.

        Returns:
            The bundle itself.
        """

        return self.set_write_mode(WriteMode.Imme)

    def set_write_mode_as_rise(self):
        """
        Set the write mode of the bundle to rise.

        Returns:
            The bundle itself.
        """

        return self.set_write_mode(WriteMode.Rise)

    def set_write_mode_as_fall(self):
        """
        Set the write mode of the bundle to fall.

        Returns:
            The bundle itself.
        """

        return self.set_write_mode(WriteMode.Fall)

    async def step(self, ncycles=1):
        """
        Wait for the clock for ncycles.
        Only works if the bundle has a connected signal.

        Args:
            ncycles: The number of cycles to wait.
        """

        if self.__clock_event is None:
            critical("cannot use step in bundle without a connected signal")

        for _ in range(ncycles):
            await self.__clock_event.wait()

    def bind(self, dut, unconnected_signal_access=True):
        """
        Bind the dut's signal to this bundle. it will overwrites the previous bind.

        Args:
            dut: The dut to bind the bundle to.
            unconnected_signal_access: Whether unconnected signals could be accessed.

        Returns:
            The bundle itself.
        """

        if self.bound:
            warning("bundle is already bound, the previous bind will be overwritten")
            self.__unbind_all()
        else:
            # When first bind, set all sub-bundles' name
            for sub_bundle_name, sub_bundle in self.__all_sub_bundles():
                sub_bundle.set_name(sub_bundle_name)

        self.__bind_from_signal_list(
            list(self.__dut_all_signals(dut)),
            self.name,
            [],
            unconnected_signal_access,
            False,
            None,
            None,
        )

        self.bound = True
        if self.write_mode is not None:
            self.__set_all_signals_write_mode(self.write_mode)
        self.make_requset_response_for(dut)
        return self

    def as_dict(self, multilevel=True):
        """
        Collect all signals values into a dictionary.

        Args:
            multilevel: When multilevel is true, the subbundle signal values are put
                        into a secondary dictionary. Otherwise, the dictionary returned
                        will have only one level, with the sub-bundles separated by dots
                        in keys.

        Returns:
            A dictionary of all signals values in the bundle.
        """

        if multilevel:
            signals = {
                signal: getattr(self, signal).value
                for signal in self.current_level_signals
            }
            signal_lists = {
                signal_list_name: [signal.value for signal in signal_list]
                for signal_list_name, signal_list in self.__all_signal_lists()
            }
            sub_bundles = {
                sub_bundle_name: getattr(self, sub_bundle_name).as_dict(multilevel)
                for sub_bundle_name, _ in self.__all_sub_bundles()
            }
            sub_bundle_lists = {
                sub_bundle_list_name: [
                    sub_bundle.as_dict(multilevel) for sub_bundle in sub_bundle_list
                ]
                for sub_bundle_list_name, sub_bundle_list in self.__all_bundle_lists()
            }
            return {**signals, **signal_lists, **sub_bundles, **sub_bundle_lists}
        else:
            signals = {
                signal: getattr(self, signal).value
                for signal in self.current_level_signals
            }
            for signal_list_name, signal_list in self.__all_signal_lists():
                signals.update(
                    {signal_list_name: [signal.value for signal in signal_list]}
                )
            for sub_bundle_name, sub_bundle in self.__all_sub_bundles():
                sub_bundle_dict = sub_bundle.as_dict(multilevel)
                for sub_bundle_signal, value in sub_bundle_dict.items():
                    signals[f"{sub_bundle_name}.{sub_bundle_signal}"] = value
            for bundle_list_name, bundle_list in self.__all_bundle_lists():
                for i, bundle in enumerate(bundle_list):
                    bundle_dict = bundle.as_dict(multilevel)
                    for bundle_signal, value in bundle_dict.items():
                        signals[f"{bundle_list_name}[{i}].{bundle_signal}"] = value
            return signals

    def set_all(self, value):
        """
        Set all signals values to a value, including sub-bundles.

        Args:
            value: The value to set.

        Returns:
            The bundle itself.
        """

        for _, signal in self.all_signals():
            if Bundle.__is_instance_of_xpin(signal) and not signal.IsOutIO():
                signal.value = value
        return self

    def randomize_all(
        self, value_range=None, exclude_signals=[], random_func=random.randint
    ):
        """
        Randomize all signals values in the bundle.

        Args:
            value_range: The range of the random values, eg. (0, 100), both values are inclusive. If None, the range
                         of random values will be the range of each signal value.
            exclude_signals: A list of signals to exclude from randomization, sub-bundle names are separated by dots.
            random_func: The random function to use, default is random.randint.
        """

        for signal_name, signal in self.all_signals():
            if (
                signal_name not in exclude_signals
                and Bundle.__is_instance_of_xpin(signal)
                and not signal.IsOutIO()
            ):
                if value_range is not None:
                    signal.value = random_func(value_range[0], value_range[1])
                else:
                    signal_width = signal.W()
                    if signal_width == 0:
                        signal_width = 1
                    signal.value = random_func(0, 2**signal_width - 1)

    def assign(self, item, multilevel=True, level_string=""):
        """
        Assign all signals values.

        Args:
            item: item can be a dict or an object with a __bundle_assign__ method defined.
                  When item is a dictionary, if "*" is in the dictionary, the value of "*"
                  will be assigned to all signals that are not in the dictionary.
                  Otherwise assign will call the __bundle_assign__ function in item to
                  complete the assignment to the bundle
            multilevel: When multilevel is true, the subbundle signal values are taken
                        from a secondary dictionary. Otherwise, the dictionary should have
                        only one level, with the sub-bundles separated by dots in keys.
        """

        # Case 1: Item is an object with __bundle_assign__ method

        if not isinstance(item, dict):
            if hasattr(item, "__bundle_assign__"):
                item.__bundle_assign__(self)
            else:
                critical(
                    "assign: item must be a dictionary or an object with __bundle_assign__ method"
                )
            return

        # Case 2: Item is a dictionary

        if "*" in item:
            self.set_all(item["*"])
            del item["*"]

        if multilevel:
            for signal, value in item.items():
                if signal in self.current_level_signals:
                    getattr(self, signal).value = value
                elif any(
                    signal_list[0] == signal
                    for signal_list in self.__all_signal_lists()
                ):
                    getattr(self, signal).assign(value)
                elif any(
                    subbundle[0] == signal for subbundle in self.__all_sub_bundles()
                ):
                    getattr(self, signal).assign(
                        value,
                        multilevel,
                        Bundle.__appended_level_string(level_string, signal),
                    )
                elif any(
                    bundle_list[0] == signal
                    for bundle_list in self.__all_bundle_lists()
                ):
                    getattr(self, signal).assign(value, multilevel)
                else:
                    full_signal_name = Bundle.__appended_level_string(
                        level_string, signal
                    )
                    error(f'assign: signal "{full_signal_name}" is not found in bundle')
        else:
            for signal, value in item.items():
                if signal in self.current_level_signals:
                    getattr(self, signal).value = value
                elif any(
                    signal_list[0] == signal
                    for signal_list in self.__all_signal_lists()
                ):
                    getattr(self, signal).assign(value)
                else:
                    sub_bundle_name = None
                    if "." in signal:
                        sub_bundle_name, sub_bundle_signal = signal.split(".", 1)

                    if sub_bundle_name in [
                        sub_bundle[0] for sub_bundle in self.__all_sub_bundles()
                    ]:
                        getattr(self, sub_bundle_name).assign(
                            {sub_bundle_signal: value},
                            multilevel,
                            Bundle.__appended_level_string(level_string, sub_bundle_name),
                        )
                    elif any(
                        bundle_list[0] == signal
                        for bundle_list in self.__all_bundle_lists()
                    ):
                        getattr(self, signal).assign(value, multilevel)
                    else:
                        full_signal_name = Bundle.__appended_level_string(
                            level_string, signal
                        )
                        error(
                            f'assign: signal "{full_signal_name}" is not found in bundle'
                        )

    def detect_connectivity(self, signal_name):
        """
        Detect wether a signal name could be connected to this bundle.

        Args:
            bundle: The bundle to connect.
            signal_name: The name of the signal to connect.

        Returns:
            True if the signal name could be connected to the bundle, False otherwise.
        """

        all_signals = [{"name": signal_name, "org_name": signal_name, "signal": None}]

        all_signals = self.__bind_from_signal_list(
            all_signals, self.name, [], False, True, None, None
        )

        return len(all_signals) == 0

    def all_signals_rule(self):
        """
        Get all signals rules in the bundle.

        Returns:
            A dictionary of all signals rules in the bundle.
        """

        all_signals_rule = {}
        self.__bind_from_signal_list(
            [], self.name, [], False, True, None, all_signals_rule
        )
        return all_signals_rule

    def detect_specific_connectivity(self, signal_name, specific_signal):
        """
        Detects whether a signal name can be connected to a specific signal in the bundle

        Args:
            signal_name: The name of the signal to connect.
            specific_signal: The specific signal in this bundle to connect to, format such
                             as "subbundle1.subbundle2.siganlA"
        """

        all_signals = [{"name": signal_name, "org_name": signal_name, "signal": None}]

        all_signals = self.__bind_from_signal_list(
            all_signals, "", [], False, True, specific_signal, None
        )

        return len(all_signals) == 0

    @classmethod
    def from_prefix(cls, prefix="", dut=None):
        """
        Create a bundle from a prefix.

        Args:
            prefix: The prefix to match the signals.

        Returns:
            A new bundle.
        """
        new_bundle = cls()
        if dut is not None:
            for attr_key in dir(dut):
                if not attr_key.startswith(prefix):
                    continue
                attr_value = getattr(dut, attr_key)
                if "XData" not in attr_value.__class__.__name__:
                    continue
                new_bundle.current_level_signals.append(attr_key[len(prefix) :])
            # new_bundle.__set_current_level_signal()
        new_bundle.__connect_method = PrefixBindMethod(prefix)
        return new_bundle

    @classmethod
    def from_regex(cls, regex=r""):
        """
        Create a bundle from a regex.
        The signals captured in the regex will be used for matching. If there are
        multiple capture groups, they are concatenated into a string to match.

        Args:
            regex: The regex to match the signals.

        Returns:
            A new bundle.
        """

        new_bundle = cls()
        new_bundle.__connect_method = RegexBindMethod(regex)
        return new_bundle

    @classmethod
    def from_dict(cls, dict={}):
        """
        Create a bundle from a dictionary.
        The keys of the dictionary are the names of the signals in the bundle,
        and the values are the names of the signals in DUT.

        Args:
            dict: The dictionary to guide bundle connections

        Returns:
            A new bundle.
        """

        new_bundle = cls()
        new_bundle.__connect_method = DictBindMethod(dict)
        new_bundle.__check_dict_value(dict)
        return new_bundle

    @staticmethod
    def new_class_from_list(signal_list):
        """
        Create a new bundle class with a list of signals quickly.

        >>> myBundle = Bundle.new_class_from_list(["a", "b", "c"]).from_prefix("io_")

        Args:
            signal_list: A list of signals.

        Returns:
            A new bundle class.
        """

        class NewBundle(Bundle):
            signals = signal_list

        return NewBundle

    @staticmethod
    def new_class_from_xport(xport):
        """
        Create a new bundle class with an XPort quickly.

        Args:
            xport: The XPort to create the bundle from.

        Returns:
            A new bundle class.
        """

        return Bundle.new_class_from_list(xport.GetKeys())

    @classmethod
    def from_xport(cls, xport):
        """
        Create a bundle from an XPort.

        Args:
            xport: The XPort to create the bundle from.

        Returns:
            A new bundle.
        """

        signal_list = xport.GetKeys()
        bundle = cls.new_class_from_list(signal_list)()
        for signal in signal_list:
            setattr(bundle, signal, xport[signal])
        return bundle

    def __set_current_level_signal(self):
        """
        Collect all signals in the current level and save them in the current_level_signals.
        """

        self.current_level_signals = [signal for signal in self.signals]

        for signal in dir(self):
            if isinstance(getattr(self, signal), Signal):
                self.current_level_signals.append(signal)

    def all_signals(self, level_string=""):
        """
        Yield all signals of the bundle.

        Returns:
            A generator of signal names and signals.
        """

        for signal in self.current_level_signals:
            yield (Bundle.__appended_level_string(level_string, signal)), getattr(
                self, signal, None
            )
        for signal_list_name, signal_list in self.__all_signal_lists():
            for idx, signal in enumerate(signal_list.signals):
                yield (
                    Bundle.__appended_level_string(
                        level_string, f"{signal_list_name}[{idx}]"
                    ),
                    signal,
                )
        for sub_bundle_name, sub_bundle in self.__all_sub_bundles():
            yield from sub_bundle.all_signals(
                Bundle.__appended_level_string(level_string, sub_bundle_name)
            )
        for bundle_list_name, bundle_list in self.__all_bundle_lists():
            for idx, bundle in enumerate(bundle_list.bundles):
                yield from bundle.all_signals(
                    Bundle.__appended_level_string(
                        level_string, f"{bundle_list_name}[{idx}]"
                    )
                )

    def __getitem__(self, key):
        """
        Get the signal by key.

        Args:
            key: The key of the signal.

        Returns:
            The signal.
        """

        return getattr(self, key, None)

    def __add_signal_attr(self, signal_name, signal, info_bundle_name, info_dut_name):
        """
        Add a signal attribute to the bundle and log the connection.

        Args:
            signal_name: The name of the signal in the bundle.
            signal: The signal itself.
            info_bundle_name: The name of the bundle in the log.
            info_dut_name: The name of the signal in the DUT in the log.
        """

        setattr(self, signal_name, signal)
        self.__update_signal_info(signal)
        info(f'dut\'s signal "{info_dut_name}" is connected to "{info_bundle_name}"')

    def __update_signal_info(self, signal):
        """
        Update the signal info when a signal is connected to the bundle.
        """

        if self.__clock_event is None:
            self.__clock_event = signal.event

        if hasattr(signal.xdata, "number_of_bundles_connected_to"):
            signal.xdata.number_of_bundles_connected_to += 1
        else:
            signal.xdata.number_of_bundles_connected_to = 1

    def __str__(self):
        item_str = ""

        signals = ", ".join(
            [
                f"{signal}: {getattr(self, signal)}"
                for signal in self.current_level_signals
            ]
        )
        signal_lists = ", ".join(
            [
                f"{signal_list_name}: {signal_list}"
                for signal_list_name, signal_list in self.__all_signal_lists()
            ]
        )
        sub_bundles = ", ".join(
            [
                f"{sub_bundle_name}: {sub_bundle}"
                for sub_bundle_name, sub_bundle in self.__all_sub_bundles()
            ]
        )
        bundle_lists = ", ".join(
            [
                f"{bundle_list_name}: {bundle_list}"
                for bundle_list_name, bundle_list in self.__all_bundle_lists()
            ]
        )

        item_str = signals
        if signal_lists != "":
            item_str = item_str + ", " + signal_lists
        if sub_bundles != "":
            item_str = item_str + ", " + sub_bundles
        if bundle_lists != "":
            item_str = item_str + ", " + bundle_lists

        return f"{type(self).__name__}({item_str})"

    def __all_sub_bundles(self):
        """
        Yield all sub-bundles of the bundle.

        Returns:
            A generator of tuples (attr_name, sub_bundle) where attr is the name of the
            sub-bundle and sub_bundle is the sub-bundle itself.
        """

        for attr in dir(self):
            if isinstance(getattr(self, attr), Bundle):
                yield (attr, getattr(self, attr))

    def __all_signal_lists(self):
        """
        Yield all signal lists of the bundle.

        Returns:
            A generator of tuples (attr_name, signal_list) where attr is the name of the
            signal list and signal_list is the signal list itself.
        """

        for attr in dir(self):
            if isinstance(getattr(self, attr), SignalList):
                yield (attr, getattr(self, attr))

    def __all_bundle_lists(self):
        """
        Yield all bundle lists of the bundle.

        Returns:
            A generator of tuples (attr_name, bundle_list) where attr is the name of the
            bundle list and bundle_list is the bundle list itself.
        """

        for attr in dir(self):
            if isinstance(getattr(self, attr), BundleList):
                yield (attr, getattr(self, attr))

    def __detect_missing_signals(
        self, connected_signals, level_string, rule_stack, unconnected_signal_access
    ):
        """
        Detect missing signals in the bundle. Log a warning if a signal is not found.
        When unconnected_signal_access is True, set the unconnected signals as dummy
        signals.

        Args:
            connected_signals: A list of connected signals.
            level_string: The string of the current level.
            unconnected_signal_access: Whether unconnected signals could be accessed.
        """

        self._dummy_signal = DummySignal()

        for signal in self.current_level_signals:
            if signal not in connected_signals:
                rule_string = Bundle.__get_rule_string(rule_stack, signal)
                warning(
                    f'The signal that can be connected to "{Bundle.__appended_level_string(level_string, signal)}" '
                    f'is not found in dut, it should satisfy rule "{rule_string}"'
                )

                if unconnected_signal_access:
                    setattr(self, signal, self._dummy_signal)

        for signal_list_name, signal_list in self.__all_signal_lists():
            for idx, signal in enumerate(signal_list.names):
                if signal not in connected_signals:
                    rule_string = Bundle.__get_rule_string(rule_stack, signal)
                    level_string = Bundle.__appended_level_string(
                        level_string, f"{signal_list_name}[{idx}]"
                    )
                    warning(
                        f'The signal that can be connected to "{level_string}" '
                        f'is not found in dut, it should satisfy rule "{rule_string}"'
                    )

                    if unconnected_signal_access:
                        signal_list.signals[idx] = self._dummy_signal

    def __remove_signal_attr(self, signal_name):
        """
        Remove a signal attribute from the bundle.

        Args:
            signal_name: The name of the signal to remove.
        """

        signal = getattr(self, signal_name)
        if hasattr(signal.xdata, "number_of_bundles_connected_to"):
            signal.xdata.number_of_bundles_connected_to -= 1
            if signal.xdata.number_of_bundles_connected_to == 0:
                delattr(signal.xdata, "number_of_bundles_connected_to")
        delattr(self, signal_name)

    def __unbind_all(self):
        """
        Unbind all signals to the bundle.
        """

        for signal_name in self.current_level_signals:
            if hasattr(self, signal_name):
                self.__remove_signal_attr(signal_name)
        for _, sub_bundle in self.__all_sub_bundles():
            sub_bundle.__unbind_all()

    def __bind_from_signal_list(
        self,
        all_signals,
        level_string,
        rule_stack,
        unconnected_signal_access,
        detection_mode,
        specific_signal,
        all_signals_rule,
    ):
        """
        Bind the signals to the bundle.

        Args:
            all_signals: A list of signals to bind.
            level_string: The string of the current level.
            rule_stack: The stack of rules to bind the signals, this is a list of bundles.
            unconnected_signal_access: Whether unconnected signals could be accessed.
            detection_mode: Whether the method is in detection mode. if it is, the bundle
                            will not be connected to the signals.
            specific_signal: It is only valid in detection mode. If it is not None, only
                             the specific signal will be connected.
            all_signals_rule: It is only valid in detection mode. If it is not None, the
                              rule of all signals will be stored in this dictionary.

        Returns:
            A list of signals that are not matched.
        """

        rule_stack = rule_stack + [self]
        connected_signals, matching_signals, remain_signals = (
            self.__connect_method.bind(self, all_signals, level_string, detection_mode)
        )

        if not detection_mode:
            self.__detect_missing_signals(
                connected_signals, level_string, rule_stack, unconnected_signal_access
            )

            if specific_signal is not None:
                error("specific signal could only be used in detection mode")
        else:
            if specific_signal is not None:
                remain_signals += connected_signals
                connected_signals = []

                for signal in remain_signals:
                    full_signal_name = Bundle.__appended_level_string(
                        level_string, signal["name"]
                    )
                    if full_signal_name == specific_signal:
                        connected_signals.append(signal)
                        remain_signals.remove(signal)

            if all_signals_rule is not None:
                for signal in self.current_level_signals:
                    full_signal_name = Bundle.__appended_level_string(
                        level_string, signal
                    )
                    rule_string = Bundle.__get_rule_string(rule_stack, signal)
                    all_signals_rule[full_signal_name] = rule_string

                for signal_list_name, signal_list in self.__all_signal_lists():
                    for idx, signal in enumerate(signal_list.names):
                        full_signal_name = Bundle.__appended_level_string(
                            level_string, f"{signal_list_name}[{idx}]"
                        )
                        rule_string = Bundle.__get_rule_string(rule_stack, signal)
                        all_signals_rule[full_signal_name] = rule_string

        # Bind the remain signals to the sub-bundles
        for sub_bundle_name, sub_bundle in self.__all_sub_bundles():
            matching_signals = sub_bundle.__bind_from_signal_list(
                matching_signals,
                Bundle.__appended_level_string(level_string, sub_bundle_name),
                rule_stack,
                unconnected_signal_access,
                detection_mode,
                specific_signal,
                all_signals_rule,
            )
            if sub_bundle.__clock_event is not None:
                self.__clock_event = sub_bundle.__clock_event

        for bundle_list_name, bundle_list in self.__all_bundle_lists():
            for idx, bundle in enumerate(bundle_list.bundles):
                matching_signals = bundle.__bind_from_signal_list(
                    matching_signals,
                    Bundle.__appended_level_string(
                        level_string, f"{bundle_list_name}[{idx}]"
                    ),
                    rule_stack,
                    unconnected_signal_access,
                    detection_mode,
                    specific_signal,
                    all_signals_rule,
                )

        Bundle.__revert_signal_name(matching_signals, all_signals)
        return matching_signals + remain_signals

    def __check_dict_value(self, dict):
        """
        Check if the values of the dictionary are valid.

        Args:
            dict: The dictionary to check.
        """

        signal_list = [
            {"name": value, "org_name": key, "signal": None}
            for key, value in dict.items()
        ]

        unconnected_signals = self.__bind_from_signal_list(
            signal_list, self.name, [], False, True, None, None
        )
        unconnected_signal_names = [
            signal["org_name"] for signal in unconnected_signals
        ]

        if len(unconnected_signal_names) > 0:
            warning(
                f"The signal names {unconnected_signal_names} in {type(self).__name__}'s connection dictionary "
                "are invalid, because they cannot match any signals from the Bundle"
            )

    @staticmethod
    def detect_unconnected_signals(dut):
        """
        Detect signals that are not connected to any bundle.

        Args:
            dut: The dut to detect.

        Returns:
            A list of unconnected signals
        """

        unconnected_signals = []
        for signal_info in Bundle.__dut_all_signals(dut):
            signal = signal_info["signal"]
            if (
                not hasattr(signal, "number_of_bundles_connected_to")
                or signal.number_of_bundles_connected_to == 0
            ):
                unconnected_signals.append(signal_info["org_name"])

        return unconnected_signals

    @staticmethod
    def detect_multiple_connections(dut):
        """
        Detect signals that are connected to multiple bundles.

        Args:
            dut: The dut to detect.

        Returns:
            A list of signals that are connected to multiple bundles.
        """

        multiple_connections = []
        for signal_info in Bundle.__dut_all_signals(dut):
            signal = signal_info["signal"]
            if (
                hasattr(signal, "number_of_bundles_connected_to")
                and signal.number_of_bundles_connected_to > 1
            ):
                multiple_connections.append(signal_info["org_name"])

        return multiple_connections

    @staticmethod
    def __appended_level_string(level_string, level):
        """
        Append a string to the current level string.

        Args:
            level_string: The current level string.
            level: The string to append.

        Returns:
            The appended string.
        """

        if level_string == "":
            return level
        else:
            return f"{level_string}.{level}"

    @staticmethod
    def __revert_signal_name(signal_list, last_signal_list):
        """
        Change the item's name in signal_list to the item's name in last_signal_list, if it has the
        same original name.

        Args:
            signal_list: A list of signals to revert. this is a list of dictionaries
                         containing the name, original name, and signal pin of the signal.
            last_signal_list: The structure of the last signal list is the same as the signal_list.
        """

        for signal in signal_list:
            for last_signal in last_signal_list:
                if signal["org_name"] == last_signal["org_name"]:
                    signal["name"] = last_signal["name"]

    @staticmethod
    def __is_instance_of_xpin(signal):
        """
        Check if the signal is an instance of XPin. XPin is a class from dut generated by picker
        that represents a signal in a simulation.

        Args:
            signal: The signal to check.

        Returns:
            True if the signal is an instance of XPin, False otherwise.
        """

        return (
            signal is not None
            and not isinstance(signal, DummySignal)
            and hasattr(signal, "xdata")
            and hasattr(signal, "event")
        )

    @staticmethod
    def __dut_all_signals(dut):
        """
        Yield all signals of the dut.

        Args:
            dut: The dut to get signals from.

        Returns:
            A generator of dictionaries containing the name, original name, and signal of the signal.
        """

        for attr in dir(dut):
            signal = getattr(dut, attr)
            if not callable(signal) and Bundle.__is_instance_of_xpin(signal):
                yield {"name": attr, "org_name": attr, "signal": signal}

    @staticmethod
    def __get_rule_string(rule_stack, signal):
        """
        Get the rule string from the rule stack.

        Args:
            rule_stack: The stack of rules, this is a list of bundles.
            signal: The signal to get the rule string.

        Returns:
            The rule string.
        """

        rule_string = ""
        rule_stack = rule_stack + [signal]

        for index, rule in enumerate(rule_stack):

            # Prefix
            if rule is rule_stack[-1] or rule.__connect_method.method == "prefix":
                rule_string += (
                    rule
                    if rule is rule_stack[-1]
                    else rule.__connect_method.method_value
                )

            # Dict
            elif rule.__connect_method.method == "dict":
                relative_signal_name = Bundle.__get_path_from_rule_stack(
                    rule_stack[index:]
                )
                flitered_dict = rule_stack[index].__filter_signals_in_dict(
                    rule.__connect_method.method_value, relative_signal_name
                )

                dict_string = f"<No Matching Signal in Dict>"
                if len(flitered_dict) > 0:
                    dict_string = "|".join(
                        [f"{value}" for _, value in flitered_dict.items()]
                    )
                    if len(flitered_dict) > 1:
                        dict_string = f"({dict_string})"

                rule_string += dict_string
                break

            # Regex
            elif rule.__connect_method.method == "regex":
                rule_string += f"{rule.__connect_method.method_value} -> "

            else:
                raise ValueError(f"rule must be 'dict', 'prefix', or 'regex'")

        return rule_string

    def __filter_signals_in_dict(self, dict, could_connected_to):
        """
        Filter signals in the dictionary that could be connected to a specific signal.

        Args:
            dict: The dictionary to filter.
            could_connected_to: The specific signal to connect to.

        Returns:
            A dictionary of signals that could be connected to the specific signal.
        """

        filtered_dict = {}
        for key, value in dict.items():
            if self.detect_specific_connectivity(key, could_connected_to):
                filtered_dict[key] = value
        return filtered_dict

    @staticmethod
    def __get_path_from_rule_stack(rule_stack, signal=""):
        """
        Get siganl path from the rule stack.

        Args:
            rule_stack: The stack of rules, this is a list of bundles.
            signal: The signal to get the path.
        """

        path = ""
        if signal != "":
            rule_stack = rule_stack + [signal]

        for rule in rule_stack[1:]:
            if rule is rule_stack[-1]:
                path += rule
            else:
                path += rule.name + "."

        return path
