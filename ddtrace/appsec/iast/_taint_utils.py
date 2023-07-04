#!/usr/bin/env python3
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec.iast._taint_tracking import taint_pyobject
from ddtrace.internal.logger import get_logger


DBAPI_INTEGRATIONS = ("sqlite", "psycopg", "mysql", "mariadb")
DBAPI_PREFIXES = ("django-",)

log = get_logger(__name__)


class LazyTaintList(list):
    def __init__(self, *args, origins=(0, 0), override_pyobject_tainted=False):
        self.origins = origins
        self.origin_value = origins[1]
        self.override_pyobject_tainted = override_pyobject_tainted
        super(LazyTaintList, self).__init__(*args)

    def __getitem__(self, key):
        value = super(LazyTaintList, self).__getitem__(key)
        if value:
            if isinstance(value, dict) and not isinstance(value, LazyTaintDict):
                value = LazyTaintDict(
                    value, origins=self.origins, override_pyobject_tainted=self.override_pyobject_tainted
                )
                self[key] = value
            elif isinstance(value, list) and not isinstance(value, LazyTaintList):
                value = LazyTaintList(
                    value, origins=self.origins, override_pyobject_tainted=self.override_pyobject_tainted
                )
                if isinstance(key, int):
                    self[key] = value
            elif isinstance(value, (str, bytes, bytearray)):
                if not is_pyobject_tainted(value) or self.override_pyobject_tainted:
                    try:
                        value = taint_pyobject(
                            pyobject=value, source_name=key, source_value=value, source_origin=self.origin_value
                        )
                        self[key] = value
                    except SystemError:
                        # TODO: Find the root cause for
                        # SystemError: NULL object passed to Py_BuildValue
                        log.debug("SystemError while tainting value: %s with key: %s", value, key, exc_info=True)
                    except Exception:
                        log.debug("Unexpected exception while tainting value", exc_info=True)
        return value

    def __iter__(self):
        return (self[i] for i in range(len(self)))


class LazyTaintDict(dict):
    def __init__(self, *args, origins=(0, 0), override_pyobject_tainted=False):
        self.origins = origins
        self.origin_key = origins[0]
        self.origin_value = origins[1]
        self.override_pyobject_tainted = override_pyobject_tainted
        super(LazyTaintDict, self).__init__(*args)

    def __getitem__(self, key):
        value = super(LazyTaintDict, self).__getitem__(key)
        if value:
            if isinstance(value, dict) and not isinstance(value, LazyTaintDict):
                value = LazyTaintDict(
                    value, origins=self.origins, override_pyobject_tainted=self.override_pyobject_tainted
                )
                self[key] = value
            elif isinstance(value, list) and not isinstance(value, LazyTaintList):
                value = LazyTaintList(
                    value, origins=self.origins, override_pyobject_tainted=self.override_pyobject_tainted
                )
                self[key] = value
            elif isinstance(value, (str, bytes, bytearray)):
                if not is_pyobject_tainted(value) or self.override_pyobject_tainted:
                    try:
                        value = taint_pyobject(
                            pyobject=value, source_name=key, source_value=value, source_origin=self.origin_value
                        )
                        super(LazyTaintDict, self).__setitem__(key, value)
                    except SystemError:
                        # TODO: Find the root cause for
                        # SystemError: NULL object passed to Py_BuildValue
                        log.debug("SystemError while tainting value: %s with key: %s", value, key, exc_info=True)
                    except Exception:
                        log.debug("Unexpected exception while tainting value", exc_info=True)
        return value

    def get(self, key, default=None):
        try:
            return self.__getitem__(key)
        except KeyError:
            pass
        return default

    def items(self):
        for k in self.keys():
            yield (k, self[k])

    def keys(self):
        for k in super(LazyTaintDict, self).keys():
            if (
                k
                and isinstance(k, (str, bytes, bytearray))
                and (self.override_pyobject_tainted or not is_pyobject_tainted(k))
            ):
                try:
                    k = taint_pyobject(pyobject=k, source_name=k, source_value=k, source_origin=self.origin_key)
                except Exception:
                    log.debug("Unexpected exception while tainting key", exc_info=True)
            yield k

    def values(self):
        for _, v in self.items():
            yield v


def supported_dbapi_integration(integration_name):
    return integration_name in DBAPI_INTEGRATIONS or integration_name.startswith(DBAPI_PREFIXES)


def check_tainted_args(args, kwargs, tracer, integration_name, method):
    if supported_dbapi_integration(integration_name) and method.__name__ == "execute":
        return len(args) and args[0] and is_pyobject_tainted(args[0])

    return False
