import os
import mock
import ddtrace

from nose.tools import ok_
from contextlib import contextmanager


class FakeTime(object):
    """"Allow to mock time.time for tests

    `time.time` returns a defined `current_time` instead.
    Any `time.time` call also increase the `current_time` of `delta` seconds.
    """

    def __init__(self):
        # Sane defaults
        self._current_time = 1e9
        self._delta = 0.001

    def __call__(self):
        self._current_time = self._current_time + self._delta
        return self._current_time

    def set_epoch(self, epoch):
        self._current_time = epoch

    def set_delta(self, delta):
        self._delta = delta

    def sleep(self, second):
        self._current_time += second


def patch_time():
    """Patch time.time with FakeTime"""
    return mock.patch('time.time', new_callable=FakeTime)


def assert_dict_issuperset(a, b):
    ok_(set(a.items()).issuperset(set(b.items())),
            msg="{a} is not a superset of {b}".format(a=a, b=b))


def assert_list_issuperset(a, b):
    ok_(set(a).issuperset(set(b)),
            msg="{a} is not a superset of {b}".format(a=a, b=b))


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


@contextmanager
def set_env(**environ):
    """
    Temporarily set the process environment variables.

    >>> with set_env(DEFAULT_SERVICE='my-webapp'):
            # your test
    """
    old_environ = dict(os.environ)
    os.environ.update(environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_environ)
