#!/usr/bin/env python3
from ddtrace.appsec.iast._input_info import Input_info
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import taint_pyobject  # type: ignore[attr-defined]


class LazyTaintDict(dict):
    def __getitem__(self, key):
        value = super(LazyTaintDict, self).__getitem__(key)
        if isinstance(value, (str, bytes, bytearray)) and not is_pyobject_tainted(value):
            value = taint_pyobject(value, Input_info(key, value, 0))
            super(LazyTaintDict, self).__setitem__(key, value)
        return value

    def get(self, key, default=None):
        try:
            return self.__getitem__(key)
        except KeyError:
            pass
        return default

    def items(self):
        for k, v in super(LazyTaintDict, self).items():
            if isinstance(v, (str, bytes, bytearray)) and not is_pyobject_tainted(v):
                v = taint_pyobject(v, Input_info(k, v, 0))
                super(LazyTaintDict, self).__setitem__(k, v)

            yield (k, v)

    def values(self):
        for k, v in self.items():
            yield v
