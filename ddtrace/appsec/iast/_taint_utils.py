#!/usr/bin/env python3
from ddtrace.appsec.iast._input_info import Input_info
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec.iast._taint_tracking import taint_pyobject


DBAPI_INTEGRATIONS = ("sqlite", "psycopg", "mysql", "mariadb")


class LazyTaintDict(dict):
    def __getitem__(self, key):
        value = super(LazyTaintDict, self).__getitem__(key)
        if value and isinstance(value, (str, bytes, bytearray)) and not is_pyobject_tainted(value):
            try:
                value = taint_pyobject(value, Input_info(key, value, 0))
                super(LazyTaintDict, self).__setitem__(key, value)
            except SystemError:
                # TODO: Find the root cause for
                # SystemError: NULL object passed to Py_BuildValue
                pass
        return value

    def get(self, key, default=None):
        try:
            return self.__getitem__(key)
        except KeyError:
            pass
        return default

    def items(self):
        for k, v in super(LazyTaintDict, self).items():
            if v and isinstance(v, (str, bytes, bytearray)) and not is_pyobject_tainted(v):
                try:
                    v = taint_pyobject(v, Input_info(k, v, 0))
                    super(LazyTaintDict, self).__setitem__(k, v)
                except SystemError:
                    # TODO: Find the root cause for
                    # SystemError: NULL object passed to Py_BuildValue
                    pass

            yield (k, v)

    def values(self):
        for k, v in self.items():
            yield v


def check_tainted_args(args, kwargs, tracer, integration_name, method):
    if integration_name in DBAPI_INTEGRATIONS and method.__name__ == "execute":
        return args[0] and is_pyobject_tainted(args[0])

    return False
