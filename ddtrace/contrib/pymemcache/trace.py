# 3p
import wrapt
import six
import sys
from pymemcache.client.base import Client
from pymemcache.client.hash import HashClient
from pymemcache.exceptions import (
    MemcacheClientError,
    MemcacheServerError,
    MemcacheUnknownCommandError,
    MemcacheUnknownError,
    MemcacheIllegalInputError,
)

# project
from ddtrace import Pin
from ddtrace.ext import memcached as memcachedx

# keep a reference to the original unpatched clients
_Client = Client
_HashClient = HashClient


class WrappedClient(wrapt.ObjectProxy):
    def __init__(self, *args, **kwargs):
        c = _Client(*args, **kwargs)
        super(WrappedClient, self).__init__(c)

    def set(self, *args, **kwargs):
        return self._cmd("set", *args, **kwargs)

    def set_many(self, *args, **kwargs):
        return self._cmd("set_many", *args, **kwargs)

    def add(self, *args, **kwargs):
        return self._cmd("add", *args, **kwargs)

    def replace(self, *args, **kwargs):
        return self._cmd("replace", *args, **kwargs)

    def append(self, *args, **kwargs):
        return self._cmd("append", *args, **kwargs)

    def prepend(self, *args, **kwargs):
        return self._cmd("prepend", *args, **kwargs)

    def cas(self, *args, **kwargs):
        return self._cmd("cas", *args, **kwargs)

    def get(self, *args, **kwargs):
        return self._cmd("get", *args, **kwargs)

    def get_many(self, *args, **kwargs):
        return self._cmd("get_many", *args, **kwargs)

    def gets(self, *args, **kwargs):
        return self._cmd("gets", *args, **kwargs)

    def gets_many(self, *args, **kwargs):
        return self._cmd("gets_many", *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._cmd("delete", *args, **kwargs)

    def delete_many(self, *args, **kwargs):
        return self._cmd("delete_many", *args, **kwargs)

    def incr(self, *args, **kwargs):
        return self._cmd("incr", *args, **kwargs)

    def decr(self, *args, **kwargs):
        return self._cmd("decr", *args, **kwargs)

    def touch(self, *args, **kwargs):
        return self._cmd("touch", *args, **kwargs)

    def stats(self, *args, **kwargs):
        return self._cmd("stats", *args, **kwargs)

    def version(self, *args, **kwargs):
        return self._cmd("version", *args, **kwargs)

    def flush_all(self, *args, **kwargs):
        return self._cmd("flush_all", *args, **kwargs)

    def quit(self, *args, **kwargs):
        return self._cmd("quit", *args, **kwargs)

    def _cmd(self, method_name, *args, **kwargs):
        """Run and trace the given command.

        Any pymemcache exception is caught and span error information is
        set. The exception is then reraised for the application to handle
        appropriately.
        """
        method = getattr(self.__wrapped__, method_name)
        with self._span(method_name) as span:
            try:
                return method(*args, **kwargs)
            except MemcacheClientError:
                (typ, val, tb) = sys.exc_info()
                span.set_exc_info(typ, val, tb)
                six.reraise(typ, val, tb)
            except MemcacheServerError:
                (typ, val, tb) = sys.exc_info()
                span.set_exc_info(typ, val, tb)
                six.reraise(typ, val, tb)
            except MemcacheUnknownCommandError:
                (typ, val, tb) = sys.exc_info()
                span.set_exc_info(typ, val, tb)
                six.reraise(typ, val, tb)
            except MemcacheUnknownError:
                (typ, val, tb) = sys.exc_info()
                span.set_exc_info(typ, val, tb)
                six.reraise(typ, val, tb)
            except MemcacheIllegalInputError:
                (typ, val, tb) = sys.exc_info()
                span.set_exc_info(typ, val, tb)
                six.reraise(typ, val, tb)

    def _span(self, cmd_name):
        """ Return a newly created span for the given command. """
        p = self._get_pin()
        return p.tracer.trace(
            memcachedx.CMD,
            service=memcachedx.SERVICE,
            resource=cmd_name,
            span_type=memcachedx.TYPE,
        )

    def _get_pin(self):
        p = Pin.get_from(self)
        if not p:
            p = Pin(service=memcachedx.SERVICE, app_type=memcachedx.TYPE)
            p.onto(self)
        return p
