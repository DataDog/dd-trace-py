#!/usr/bin/env python3
from collections import abc

from ddtrace.internal.logger import get_logger


DBAPI_INTEGRATIONS = ("sqlite", "psycopg", "mysql", "mariadb")
DBAPI_PREFIXES = ("django-",)

log = get_logger(__name__)


def _is_tainted_struct(obj):
    return hasattr(obj, "_origins")


class LazyTaintList:
    """
    Encapsulate a list to lazily taint all content on any depth
    It will appear and act as the original list except for some additional private fields
    """

    def __init__(self, original_list, origins=(0, 0), override_pyobject_tainted=False, source_name="[]"):
        self._obj = original_list._obj if _is_tainted_struct(original_list) else original_list
        self._origins = origins
        self._origin_value = origins[1]
        self._override_pyobject_tainted = override_pyobject_tainted
        self._source_name = source_name

    def _taint(self, value):
        if value:
            if isinstance(value, (str, bytes, bytearray)):
                from ._taint_tracking import is_pyobject_tainted
                from ._taint_tracking import taint_pyobject

                if not is_pyobject_tainted(value) or self._override_pyobject_tainted:
                    try:
                        # TODO: migrate this part to shift ranges instead of creating a new one
                        value = taint_pyobject(
                            pyobject=value,
                            source_name=self._source_name,
                            source_value=value,
                            source_origin=self._origin_value,
                        )
                    except SystemError:
                        # TODO: Find the root cause for
                        # SystemError: NULL object passed to Py_BuildValue
                        log.debug("IAST SystemError while tainting value: %s", value, exc_info=True)
                    except Exception:
                        log.debug("IAST Unexpected exception while tainting value", exc_info=True)
            elif isinstance(value, abc.Mapping) and not _is_tainted_struct(value):
                value = LazyTaintDict(
                    value, origins=self._origins, override_pyobject_tainted=self._override_pyobject_tainted
                )
            elif isinstance(value, abc.Sequence) and not _is_tainted_struct(value):
                value = LazyTaintList(
                    value,
                    origins=self._origins,
                    override_pyobject_tainted=self._override_pyobject_tainted,
                    source_name=self._source_name,
                )
        return value

    def __add__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return LazyTaintList(
            self._obj + other,
            origins=self._origins,
            override_pyobject_tainted=self._override_pyobject_tainted,
            source_name=self._source_name,
        )

    @property  # type: ignore
    def __class__(self):
        return list

    def __contains__(self, item):
        return item in self._obj

    def __delitem__(self, key):
        del self._obj[key]

    def __eq__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj == other

    def __ge__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj >= other

    def __getitem__(self, key):
        return self._taint(self._obj[key])

    def __gt__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj > other

    def __iadd__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        self._obj += other

    def __imul__(self, other):
        self._obj *= other

    def __iter__(self):
        return (self[i] for i in range(len(self._obj)))

    def __le__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj <= other

    def __len__(self):
        return len(self._obj)

    def __lt__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj < other

    def __mul__(self, other):
        return LazyTaintList(
            self._obj * other,
            origins=self._origins,
            override_pyobject_tainted=self._override_pyobject_tainted,
            source_name=self._source_name,
        )

    def __ne__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj != other

    def __repr__(self):
        return repr(self._obj)

    def __reversed__(self):
        return (self[i] for i in reversed(range(len(self._obj))))

    def __setitem__(self, key, value):
        self._obj[key] = value

    def __str__(self):
        return str(self._obj)

    def append(self, item):
        self._obj.append(item)

    def clear(self):
        # TODO: stop tainting in this case
        self._obj.clear()

    def copy(self):
        return LazyTaintList(
            self._obj.copy(),
            origins=self._origins,
            override_pyobject_tainted=self._override_pyobject_tainted,
            source_name=self._source_name,
        )

    def count(self, *args):
        return self._obj.count(*args)

    def extend(self, *args):
        return self._obj.extend(*args)

    def index(self, *args):
        return self._obj.index(*args)

    def insert(self, *args):
        return self._obj.insert(*args)

    def pop(self, *args):
        return self._taint(self._obj.pop(*args))

    def remove(self, *args):
        return self._obj.remove(*args)

    def reverse(self, *args):
        return self._obj.reverse(*args)

    def sort(self, *args):
        return self._obj.sort(*args)


class LazyTaintDict:
    def __init__(self, original_dict, origins=(0, 0), override_pyobject_tainted=False):
        self._obj = original_dict
        self._origins = origins
        self._origin_key = origins[0]
        self._origin_value = origins[1]
        self._override_pyobject_tainted = override_pyobject_tainted

    def _taint(self, value, key, origin=None):
        if origin is None:
            origin = self._origin_value
        if value:
            if isinstance(value, (str, bytes, bytearray)):
                from ._taint_tracking import is_pyobject_tainted
                from ._taint_tracking import taint_pyobject

                if not is_pyobject_tainted(value) or self._override_pyobject_tainted:
                    try:
                        # TODO: migrate this part to shift ranges instead of creating a new one
                        value = taint_pyobject(
                            pyobject=value,
                            source_name=key,
                            source_value=value,
                            source_origin=origin,
                        )
                    except SystemError:
                        # TODO: Find the root cause for
                        # SystemError: NULL object passed to Py_BuildValue
                        log.debug("IAST SystemError while tainting value: %s", value, exc_info=True)
                    except Exception:
                        log.debug("IAST Unexpected exception while tainting value", exc_info=True)
            elif isinstance(value, abc.Mapping) and not _is_tainted_struct(value):
                value = LazyTaintDict(
                    value, origins=self._origins, override_pyobject_tainted=self._override_pyobject_tainted
                )
            elif isinstance(value, abc.Sequence) and not _is_tainted_struct(value):
                value = LazyTaintList(
                    value,
                    origins=self._origins,
                    override_pyobject_tainted=self._override_pyobject_tainted,
                    source_name=key,
                )
        return value

    @property  # type: ignore
    def __class__(self):
        return dict

    def __contains__(self, item):
        return item in self._obj

    def __delitem__(self, key):
        del self._obj[key]

    def __eq__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj == other

    def __ge__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj >= other

    def __getitem__(self, key):
        return self._taint(self._obj[key], key)

    def __gt__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj > other

    def __ior__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        self._obj |= other

    def __iter__(self):
        return iter(self.keys())

    def __le__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj <= other

    def __len__(self):
        return len(self._obj)

    def __lt__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj < other

    def __ne__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return self._obj != other

    def __or__(self, other):
        if _is_tainted_struct(other):
            other = other._obj
        return LazyTaintDict(
            self._obj | other,
            origins=self._origins,
            override_pyobject_tainted=self._override_pyobject_tainted,
        )

    def __repr__(self):
        return repr(self._obj)

    def __reversed__(self):
        return reversed(self.keys())

    def __setitem__(self, key, value):
        self._obj[key] = value

    def __str__(self):
        return str(self._obj)

    def clear(self):
        # TODO: stop tainting in this case
        self._obj.clear()

    def copy(self):
        return LazyTaintDict(
            self._obj.copy(),
            origins=self._origins,
            override_pyobject_tainted=self._override_pyobject_tainted,
        )

    @classmethod
    def fromkeys(cls, *args):
        return dict.fromkeys(*args)

    def get(self, key, default=None):
        observer = object()
        res = self._obj.get(key, observer)
        if res is observer:
            return default
        return self._taint(res, key)

    def items(self):
        for k in self.keys():
            yield (k, self[k])

    def keys(self):
        for k in self._obj.keys():
            yield self._taint(k, k, self._origin_key)

    def pop(self, *args):
        return self._taint(self._obj.pop(*args), "pop")

    def popitem(self):
        k, v = self._obj.popitem()
        return self._taint(k, k), self._taint(v, k)

    def remove(self, *args):
        return self._obj.remove(*args)

    def setdefault(self, *args):
        return self._taint(self._obj.setdefault(*args), args[0])

    def update(self, *args, **kargs):
        self._obj.update(*args, **kargs)

    def values(self):
        for _, v in self.items():
            yield v

    # Django Query Dict support
    def getlist(self, key, default=None):
        return self._taint(self._obj.getlist(key, default=default), key)

    def setlist(self, key, list_):
        self._obj.setlist(key, list_)

    def appendlist(self, key, item):
        self._obj.appendlist(key, item)

    def setlistdefault(self, key, default_list=None):
        return self._taint(self._obj.setlistdefault(key, default_list=default_list), key)

    def lists(self):
        return self._taint(self._obj.lists(), self._origin_value)

    def dict(self):
        return self

    def urlencode(self, safe=None):
        return self._taint(self._obj.urlencode(safe=safe), self._origin_value)


def supported_dbapi_integration(integration_name):
    return integration_name in DBAPI_INTEGRATIONS or integration_name.startswith(DBAPI_PREFIXES)


def check_tainted_args(args, kwargs, tracer, integration_name, method):
    if supported_dbapi_integration(integration_name) and method.__name__ == "execute":
        from ._taint_tracking import is_pyobject_tainted

        return len(args) and args[0] and is_pyobject_tainted(args[0])

    return False
