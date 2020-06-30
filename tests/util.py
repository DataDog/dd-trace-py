from contextlib import contextmanager
import inspect
import functools
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


class SnapshotFailed(Exception):
    pass


def snapshot(ignores=None, tracer=ddtrace.tracer):
    """Performs a snapshot integration test with the testing agent.

    All traces sent to the agent will be recorded and compared to a snapshot
    created for the test case.

    :param ignores: A list of keys to ignore when comparing snapshots. To refer
                    to keys in the meta or metrics maps use "meta.key" and
                    "metrics.key"
    :param tracer: A tracer providing the agent connection information to use.
    """
    ignores = ignores or []

    def dec(f):

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if len(args) > 1:
                self = args[0]
                clsname = self.__class__.__name__
            else:
                clsname = ""

            module = inspect.getmodule(f)

            # Use the fully qualified function name as a unique test token to
            # identify the snapshot.
            token = "{}{}{}.{}".format(module.__name__, "." if clsname else "", clsname, f.__name__)

            conn = httplib.HTTPConnection(tracer.writer.api.hostname, tracer.writer.api.port)
            try:
                # Signal the start of this test case to the test agent.
                try:
                    conn.request("GET", "/test/start?token=%s" % token)
                except Exception as e:
                    pytest.fail("Could not connect to test agent: %s" % str(e), pytrace=False)

                r = conn.getresponse()
                if r.status != 200:
                    # The test agent returns nice error messages we can forward to the user.
                    raise SnapshotFailed(r.read().decode())

                # Run the test.
                ret = f(*args, **kwargs)

                # Flush out any remnant traces.
                tracer.writer.flush_queue()

                # Query for the results of the test.
                conn = httplib.HTTPConnection(tracer.writer.api.hostname, tracer.writer.api.port)
                conn.request("GET", "/test/snapshot?ignores=%s&token=%s" % (",".join(ignores), token))
                r = conn.getresponse()
                if r.status != 200:
                    raise SnapshotFailed(r.read().decode())
                return ret
            except SnapshotFailed as e:
                # Fail the test if a failure has occurred and print out the
                # message we got from the test agent.
                pytest.fail(str(e), pytrace=False)
            finally:
                conn.close()
        return wrapper
    return dec
