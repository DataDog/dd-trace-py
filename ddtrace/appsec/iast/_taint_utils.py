#!/usr/bin/env python3
import threading

from ddtrace.appsec.iast._input_info import Input_info
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import taint_pyobject  # type: ignore[attr-defined]


DBAPI_INTEGRATIONS = ("sqlite", "psycopg", "mysql", "mariadb")


class LazyTaintDict(dict):
    def __getitem__(self, key):
        thread_id = threading.current_thread().ident
        value = super(LazyTaintDict, self).__getitem__(key)
        if value and isinstance(value, (str, bytes, bytearray)) and not is_pyobject_tainted(value, thread_id):
            try:
                value = taint_pyobject(value, Input_info(key, value, 0), thread_id)
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
        thread_id = threading.current_thread().ident
        for k, v in super(LazyTaintDict, self).items():
            if v and isinstance(v, (str, bytes, bytearray)) and not is_pyobject_tainted(v, thread_id):
                try:
                    v = taint_pyobject(v, Input_info(k, v, 0), thread_id)
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
        return args[0] and is_pyobject_tainted(args[0], threading.current_thread().ident)

    return False
