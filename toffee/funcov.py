__all__ = [
    "CovCondition",
    "CovEq",
    "CovGt",
    "CovLt",
    "CovGe",
    "CovLe",
    "CovNe",
    "CovIn",
    "CovNotIn",
    "CovIsInRange",
    "Eq",
    "Gt",
    "Lt",
    "Ge",
    "Le",
    "Ne",
    "In",
    "NotIn",
    "IsInRange",
    "CovGroup",
]


import inspect
import json
from typing import Union
from collections import OrderedDict
from typing import Callable
from typing import Union
from ._base import MObject


class CovCondition(MObject):
    """
    CovCondition class
    """

    def __check__(self, target) -> bool:
        raise NotImplementedError("Method __check__ is not implemented")

    def __call__(self, target) -> bool:
        value = getattr(target, "value", target)
        return self.__check__(value)


class CovEq(CovCondition):
    """
    CovEq class, check if the target is equal to the value
    """

    def __init__(self, value) -> None:
        self.value = value

    def __check__(self, target) -> bool:
        return target == self.value


class CovGt(CovCondition):
    """
    CovGt class, check if the target is greater than the value
    """

    def __init__(self, value) -> None:
        self.value = value

    def __check__(self, target) -> bool:
        return target > self.value


class CovLt(CovCondition):
    """
    CovLt class, check if the target is less than the value
    """

    def __init__(self, value) -> None:
        self.value = value

    def __check__(self, target) -> bool:
        return target < self.value


class CovGe(CovCondition):
    """
    CovGe class, check if the target is greater or equal to the value
    """

    def __init__(self, value) -> None:
        self.value = value

    def __check__(self, target) -> bool:
        return target >= self.value


class CovLe(CovCondition):
    """
    CovLe class, check if the target is less or equal to the value
    """

    def __init__(self, value) -> None:
        self.value = value

    def __check__(self, target) -> bool:
        return target <= self.value


class CovNe(CovCondition):
    """
    CovNe class, check if the target is not equal to the value
    """

    def __init__(self, value) -> None:
        self.value = value

    def __check__(self, target) -> bool:
        return target != self.value


class CovIn(CovCondition):
    """
    CovIn class, check if the target is in the value
    """

    def __init__(self, value) -> None:
        self.value = value

    def __check__(self, target) -> bool:
        return target in self.value


class CovNotIn(CovCondition):
    """
    CovNotIn class, check if the target is not in the value
    """

    def __init__(self, value) -> None:
        self.value = value

    def __check__(self, target) -> bool:
        return target not in self.value


class CovIsInRange(CovCondition):
    """
    CovIsInRange class, check if the target is in the range
    """

    def __init__(self, low, high) -> None:
        self.low = low
        self.high = high

    def __check__(self, target) -> bool:
        return self.low <= target <= self.high


# aliases
Eq = CovEq
Gt = CovGt
Lt = CovLt
Ge = CovGe
Le = CovLe
Ne = CovNe
In = CovIn
NotIn = CovNotIn
IsInRange = CovIsInRange


class CovGroup(object):
    """
    functional coverage group
    """

    def __init__(self, name: str = "", disable_sample_when_point_hinted=True) -> None:
        """
        CovGroup constructor
        @param name: name of the group
        @param disable_sample_when_point_hinted: if True, the group will stop sampling when all points are hinted
        """
        frame = inspect.stack()[1]
        self.filename = frame.filename
        self.lineno = frame.lineno
        self.name = name if name else "%s:%s" % (self.filename, self.lineno)
        self.disable_sample_when_point_hinted = disable_sample_when_point_hinted
        self.init()

    def init(self):
        self.cov_points = OrderedDict()
        self.hinted = False
        self.all_once = False
        self.stop_sample = False
        self.sample_count = 0
        self.sample_calln = 0
        return self

    def add_watch_point(
        self,
        target: object,
        bins: Union[dict, CovCondition, Callable[[object, object], bool]],
        name: str = "",
        once=None,
        dynamic_bin=False,
    ):
        """
        Add a watch point to the group
        @param target: the object to be watched, need to have a value attribute. eg target.value is available
        @param bins: a dict of CovCondition objects, a single CovCondition object or a Callable object (its params is call(target) -> bool).
        @param name: the name of the point
        """
        key = name
        if not key:
            key = "%s:%s" % (target, bins.keys())
        if key in self.cov_points:
            raise ValueError("Duplicated key %s" % key)
        if not isinstance(bins, dict):
            if not callable(bins):
                raise ValueError("Invalid value %s for key %s" % (bins, key))
            bins = {"anonymous": bins}
        for k, v in bins.items():
            if not isinstance(v, (list, tuple)):
                if not callable(v):
                    raise ValueError("Invalid value %s for key %s" % (v, k))
            else:
                for c in v:
                    if not callable(c):
                        raise ValueError("Invalid value %s for key %s" % (c, k))
        self.cov_points[key] = {
            "taget": target,
            "bins": bins,
            "dynamic_bin": dynamic_bin,
            "hints": {k: 0 for k in bins.keys()},
            "hinted": False,
            "once": self.disable_sample_when_point_hinted if once == None else once,
            "functions": {},
        }
        self.hinted = False
        return self

    add_cover_point = add_watch_point

    def del_point(self, name: str):
        """
        delete a point with name
        @param name: the name of the point
        """
        if name not in self.cov_points:
            raise ValueError("Invalid key %s" % name)
        del self.cov_points[name]
        return self

    def reset_point(self, name: str):
        """
        reset a point with name
        @param name: the name of the point
        """
        if name not in self.cov_points:
            raise ValueError("Invalid key %s" % name)
        self.cov_points[name]["hints"] = {
            k: 0 for k in self.cov_points[name]["bins"].keys()
        }
        self.cov_points[name]["hinted"] = False
        self.hinted = False
        return self

    def mark_function(
        self,
        name: str,
        func: Union[Callable, str, list],
        bin_name: Union[str, list] = None,
        raise_error=True,
    ):
        """Mark one or more functions for a point

        Description:
            By this reverse marking, record the relationship between checkpoints and
            target coverage functions to facilitate the management and analysis
            of checkpoints and test cases.

        Args:
            name (str): checkpoint name
            func (Union[Callable,str, list]): function or function list to be marked
            bin_name (Union[str, list]): bin name. Defaults to None.

        Returns:
            CovGroup: this covgroup object
        """
        try:
            point = self.cover_point(name)
        except Exception as e:
            if raise_error:
                raise e
            return self
        bin_names = bin_name
        if isinstance(bin_name, str) or bin_name is None:
            bin_names = [bin_name]
        else:
            assert isinstance(bin_name, (list, tuple))
        for b_name in bin_names:
            if b_name:
                if b_name not in point["bins"]:
                    # No wildcard checks are performed here; data statistics handle it themselves.
                    if "*" not in b_name and "?" not in b_name:
                        if raise_error:
                            raise Exception("Invalid bin name %s" % b_name)
                        else:
                            b_name = b_name + "(not fond)"
            else:
                b_name = "anonymous"
            if b_name not in point["functions"]:
                point["functions"][b_name] = set()
            if not isinstance(func, (list, tuple)):
                func = [func]
            for f in func:
                if isinstance(f, str):
                    point["functions"][b_name].add(f)
                else:
                    assert isinstance(f, Callable)
                    point["functions"][b_name].add("%s.%s" % (f.__module__, f.__name__))
        return self

    def clear(self):
        """
        clear all points
        """
        self.init()
        return self

    @staticmethod
    def __check__(points) -> bool:
        hinted = True
        onece = True
        for k, b in points["bins"].items():
            hints = points["hints"][k]
            checked = False
            if callable(b):
                checked = b(points["taget"])
            elif isinstance(b, (list, tuple)):
                checked = True
                for c in b:
                    if not c(points["taget"]):
                        checked = False
                        break
            else:
                raise ValueError(
                    "Invalid value %s for key %s, Need callable bin/bins" % (b, k)
                )
            hints += 1 if checked else 0
            if hints == 0:
                hinted = False
            if not (hinted and points["once"] == True):
                onece = False
            points["hints"][k] = hints
        points["hinted"] = hinted
        return hinted, onece

    def cover_points(self):
        """
        return the name list for all points
        """
        return self.cov_points.keys()

    def cover_point(self, key: str):
        """
        return the point with key
        @param key: the key of the point
        """
        if key not in self.cov_points:
            raise ValueError("Invalid key %s" % key)
        return self.cov_points[key]

    def is_point_covered(self, key: str) -> bool:
        """
        check if the point with key is covered
        @param key: the key of the point
        """
        if key not in self.cov_points:
            raise ValueError("Invalid key %s" % key)
        return self.cov_points[key]["hinted"]

    def is_all_covered(self) -> bool:
        """
        check if all points are covered
        """
        if self.hinted:
            return True
        for _, v in self.cov_points.items():
            if not v["hinted"]:
                return False
        return True

    def sample(self):
        """
        sample the group
        """
        self.sample_calln += 1
        if self.stop_sample:
            return
        if self.hinted and self.all_once:
            return
        self.sample_count += 1
        all_hinted = True
        self.all_once = True
        for _, v in self.cov_points.items():
            hinted, onece = self.__check__(v)
            if not hinted:
                all_hinted = False
            if not onece:
                self.all_once = False
        self.hinted = all_hinted
        return self

    def sample_stoped(self):
        """
        check if the group is stoped
        """
        if self.stop_sample:
            return True
        return self.hinted and self.all_once

    def stop_sample(self):
        """
        stop sampling
        """
        self.stop_sample = True
        return self

    def resume_sample(self):
        """
        resume sampling
        """
        self.stop_sample = False
        self.all_once = False
        return self

    def as_dict(self):
        """
        return the group as a dict
        """
        ret = OrderedDict()
        bins_hints = 0
        bins_total = 0
        points_hints = 0
        points_total = 0
        has_once = False

        def collect_bins(v):
            nonlocal bins_total, bins_hints
            bins_total += 1
            if v["hints"] > 0:
                bins_hints += 1
            return v

        def collect_points(v):
            nonlocal points_total, points_hints, has_once
            points_total += 1
            if v["hinted"]:
                points_hints += 1
            if v["once"]:
                has_once = True
            return v["hinted"]

        def collect_functions(v):
            ret = {}
            for k, d in v["functions"].items():
                ret[k] = list(d)
            return ret

        ret["points"] = [
            {
                "once": v["once"],
                "hinted": collect_points(v),
                "bins": [
                    collect_bins({"name": x, "hints": y}) for x, y in v["hints"].items()
                ],
                "name": k,
                "functions": collect_functions(v),
                "dynamic_bin": v["dynamic_bin"],
            }
            for k, v in self.cov_points.items()
        ]
        ret["name"] = self.name
        ret["hinted"] = self.hinted
        ret["bin_num_total"] = bins_total
        ret["bin_num_hints"] = bins_hints
        ret["point_num_total"] = points_total
        ret["point_num_hints"] = points_hints
        ret["has_once"] = has_once
        # other informations
        ret["__filename__"] = self.filename
        ret["__lineno__"] = self.lineno
        ret["__disable_sample_when_point_hinted__"] = (
            self.disable_sample_when_point_hinted
        )
        ret["__sample_count__"] = self.sample_count
        ret["__sample_calln__"] = self.sample_calln
        ret["__stop_sample__"] = self.stop_sample
        return ret

    def __str__(self) -> str:
        """
        return the group as a json string
        """
        return json.dumps(self.as_dict(), indent=4)
