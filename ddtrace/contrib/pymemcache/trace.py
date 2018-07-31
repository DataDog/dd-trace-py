# 3p
import wrapt
import sys
from pymemcache.client.base import Client
from pymemcache.exceptions import (
    MemcacheClientError,
    MemcacheServerError,
    MemcacheUnknownCommandError,
    MemcacheUnknownError,
    MemcacheIllegalInputError,
)

# project
from ddtrace import Pin
from ddtrace.compat import reraise
from ddtrace.ext import memcached as memcachedx

# keep a reference to the original unpatched clients
_Client = Client


class WrappedClient(wrapt.ObjectProxy):
    def __init__(self, *args, **kwargs):
        c = _Client(*args, **kwargs)
        super(WrappedClient, self).__init__(c)

        # attach the pin onto this instance
        p = Pin(service=memcachedx.SERVICE, app_type=memcachedx.TYPE)
        p.onto(self)

    def set(self, *args, **kwargs):
        return self._traced_cmd("set", *args, **kwargs)

    def set_many(self, *args, **kwargs):
        return self._traced_cmd("set_many", *args, **kwargs)

    def add(self, *args, **kwargs):
        return self._traced_cmd("add", *args, **kwargs)

    def replace(self, *args, **kwargs):
        return self._traced_cmd("replace", *args, **kwargs)

    def append(self, *args, **kwargs):
        return self._traced_cmd("append", *args, **kwargs)

    def prepend(self, *args, **kwargs):
        return self._traced_cmd("prepend", *args, **kwargs)

    def cas(self, *args, **kwargs):
        return self._traced_cmd("cas", *args, **kwargs)

    def get(self, *args, **kwargs):
        return self._traced_cmd("get", *args, **kwargs)

    def get_many(self, *args, **kwargs):
        return self._traced_cmd("get_many", *args, **kwargs)

    def gets(self, *args, **kwargs):
        return self._traced_cmd("gets", *args, **kwargs)

    def gets_many(self, *args, **kwargs):
        return self._traced_cmd("gets_many", *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._traced_cmd("delete", *args, **kwargs)

    def delete_many(self, *args, **kwargs):
        return self._traced_cmd("delete_many", *args, **kwargs)

    def incr(self, *args, **kwargs):
        return self._traced_cmd("incr", *args, **kwargs)

    def decr(self, *args, **kwargs):
        return self._traced_cmd("decr", *args, **kwargs)

    def touch(self, *args, **kwargs):
        return self._traced_cmd("touch", *args, **kwargs)

    def stats(self, *args, **kwargs):
        return self._traced_cmd("stats", *args, **kwargs)

    def version(self, *args, **kwargs):
        return self._traced_cmd("version", *args, **kwargs)

    def flush_all(self, *args, **kwargs):
        return self._traced_cmd("flush_all", *args, **kwargs)

    def quit(self, *args, **kwargs):
        return self._traced_cmd("quit", *args, **kwargs)

    def set_multi(self, *args, **kwargs):
        """set_multi is an alias for set_many"""
        return self._traced_cmd("set_many", *args, **kwargs)

    def get_multi(self, *args, **kwargs):
        """set_multi is an alias for set_many"""
        return self._traced_cmd("get_many", *args, **kwargs)

    def _traced_cmd(self, method_name, *args, **kwargs):
        """Run and trace the given command.

        Any pymemcache exception is caught and span error information is
        set. The exception is then reraised for the application to handle
        appropriately.
        """
        method = getattr(self.__wrapped__, method_name)
        p = Pin.get_from(self)
        with p.tracer.trace(
            memcachedx.CMD,
            service=memcachedx.SERVICE,
            resource=method_name,
            span_type=memcachedx.TYPE,
        ) as span:
            try:
                return method(*args, **kwargs)
            except (
                MemcacheClientError,
                MemcacheServerError,
                MemcacheUnknownCommandError,
                MemcacheUnknownError,
                MemcacheIllegalInputError,
            ):
                (typ, val, tb) = sys.exc_info()
                span.set_exc_info(typ, val, tb)
                reraise(typ, val, tb)
