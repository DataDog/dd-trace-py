from contextlib import contextmanager
import inspect
import pytest

import ddtrace
from ddtrace.compat import httplib


def assert_dict_issuperset(a, b):
    assert set(a.items()).issuperset(set(b.items())), \
        '{a} is not a superset of {b}'.format(a=a, b=b)


@contextmanager
def override_global_tracer(tracer):
    """Helper functions that overrides the global tracer available in the
    `ddtrace` package. This is required because in some `httplib` tests we
    can't get easily the PIN object attached to the `HTTPConnection` to
    replace the used tracer with a dummy tracer.
    """
    original_tracer = ddtrace.tracer
    ddtrace.tracer = tracer
    yield
    ddtrace.tracer = original_tracer


def snapshot(ignores=None, file=None, dir=None, tracer=ddtrace.tracer):
    ignores = ignores or []

    def dec(f):

        def wrapper(*args, **kwargs):
            if len(args) > 1:
                self = args[0]
                clsname = self.__class__.__name__
            else:
                clsname = ""

            module = inspect.getmodule(f)
            token = "{}{}{}.{}".format(module.__name__, "." if clsname else "", clsname, f.__name__)

            try:
                conn = httplib.HTTPConnection(tracer.writer.api.hostname, tracer.writer.api.port)
                conn.request("GET", "/test/start?token=%s" % token)
                r = conn.getresponse()
                if r.status != 200:
                    raise ValueError("", r.read().decode())

                ret = f(*args, **kwargs)
                tracer.writer.flush_queue()

                ignoresqs = ",".join(ignores)
                conn = httplib.HTTPConnection(tracer.writer.api.hostname, tracer.writer.api.port)
                conn.request("GET", "/test/snapshot?ignores=%s&token=%s" % (ignoresqs, token))
                r = conn.getresponse()
                if r.status != 200:
                    raise ValueError("", r.read().decode())
                return ret
            except ValueError as e:
                pytest.fail(e.args[1], pytrace=False)
            finally:
                conn.close()
        return wrapper
    return dec
