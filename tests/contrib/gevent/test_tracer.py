import subprocess  # noqa:I001
import threading

import gevent
import gevent.pool
import pytest


from ddtrace.constants import ERROR_MSG
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.trace import Context
from ddtrace.contrib.internal.gevent.patch import patch
from ddtrace.contrib.internal.gevent.patch import unpatch
from tests.utils import TracerTestCase

from .utils import silence_errors


@pytest.mark.subprocess()
def test_native_thread_logging_hook_survives_gevent_monkey_reimport():
    import importlib
    import sys

    import gevent
    import gevent.monkey as first_monkey

    import ddtrace  # noqa:F401

    assert first_monkey._ddtrace_logging_wrapped

    sys.modules.pop("gevent.monkey")
    del gevent.monkey
    second_monkey = importlib.import_module("gevent.monkey")

    assert second_monkey is not first_monkey
    assert second_monkey._ddtrace_logging_wrapped


@pytest.mark.subprocess(timeout=10, parametrize={"_DD_TEST_GEVENT_PATCH_FIRST": ["0", "1"]})
def test_native_thread_logging_does_not_deadlock_greenlets():
    import _thread
    import logging
    import os
    import time

    raw_get_ident = _thread.get_ident
    raw_monotonic = time.monotonic
    raw_sleep = time.sleep
    raw_start_new_thread = _thread.start_new_thread

    from gevent import monkey

    if os.getenv("_DD_TEST_GEVENT_PATCH_FIRST") == "0":
        import ddtrace.internal.logger as dd_logger

    monkey.patch_all()

    if os.getenv("_DD_TEST_GEVENT_PATCH_FIRST") == "1":
        import ddtrace.internal.logger as dd_logger

    import ddtrace.internal.gevent_logging as gevent_logging

    assert gevent_logging.gevent_threading_patched
    assert type(gevent_logging._DEFERRED_LOG_STATE_LOCK) is type(monkey.get_original("_thread", "allocate_lock")())

    import gevent

    native_ident = [None]
    native_done = [False]
    native_emits = [0]
    handled_native_records = [0]
    stop = [False]
    progress = {"greenlet": 0, "thread": 0}

    class SlowHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.devnull = os.open(os.devnull, os.O_WRONLY)

        def emit(self, record):
            os.write(self.devnull, (self.format(record) + "\n").encode())
            if record.msg == "from the native thread":
                handled_native_records[0] += 1
            if raw_get_ident() == native_ident[0]:
                native_emits[0] += 1
                raw_sleep(0.002)

    log = dd_logger.get_logger("ddtrace.test.gevent_logging")
    handler = SlowHandler()
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG)

    def native_thread_worker():
        native_ident[0] = raw_get_ident()
        while not stop[0]:
            log.debug("from the native thread")
            progress["thread"] += 1
            raw_sleep(0.0002)
        native_done[0] = True

    def greenlet_worker(n):
        while not stop[0]:
            log.debug("from greenlet %d", n)
            progress["greenlet"] += 1
            gevent.sleep(0.001)

    raw_start_new_thread(native_thread_worker, ())
    deadline = raw_monotonic() + 2
    while handled_native_records[0] == 0 and raw_monotonic() < deadline:
        gevent.sleep(0.001)
    assert handled_native_records[0] > 0

    greenlets = [gevent.spawn(greenlet_worker, n) for n in range(4)]
    deadline = raw_monotonic() + 2
    while progress["greenlet"] <= 10 and raw_monotonic() < deadline:
        gevent.sleep(0.001)
    stop[0] = True
    gevent.joinall(greenlets, timeout=2)
    deadline = raw_monotonic() + 2
    while not native_done[0] and raw_monotonic() < deadline:
        raw_sleep(0.001)

    assert progress["thread"] > 0
    assert progress["greenlet"] > 10
    assert native_emits[0] == 0
    assert native_done[0]
    log.handlers = []
    handler.close()
    os.close(handler.devnull)
    del handler


class TestGeventTracer(TracerTestCase):
    """
    Ensures that greenlets are properly traced when using
    the default Tracer.
    """

    # Lock to serialize test lifecycle and prevent gevent race conditions.
    # Gevent's cooperative multitasking can allow tearDown() to yield during tracer
    # shutdown/reinitialization, causing the next test's setUp() to run prematurely.
    _gevent_test_lock = threading.Lock()

    def setUp(self):
        """Before each test case, configure gevent patching with serialized tracer setup"""
        # Acquire lock for entire test lifecycle (setUp → test → tearDown)
        TestGeventTracer._gevent_test_lock.acquire()
        super(TestGeventTracer, self).setUp()
        patch()

    def tearDown(self):
        """After each test case, clean up gevent patching with serialized tracer teardown"""
        try:
            unpatch()
            super(TestGeventTracer, self).tearDown()
        finally:
            # Always release lock to prevent deadlocks
            TestGeventTracer._gevent_test_lock.release()

    def test_trace_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.trace("greenlet") as span:
                span.resource = "base"

        gevent.spawn(greenlet).join()
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        assert "greenlet" == traces[0][0].name
        assert "base" == traces[0][0].resource

    def test_trace_greenlet_twice(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.trace("greenlet") as span:
                span.resource = "base"

            with self.tracer.trace("greenlet2") as span:
                span.resource = "base2"

        gevent.spawn(greenlet).join()
        traces = self.pop_traces()
        assert 2 == len(traces)
        assert 1 == len(traces[0]) == len(traces[1])
        assert "greenlet" == traces[0][0].name
        assert "base" == traces[0][0].resource
        assert "greenlet2" == traces[1][0].name
        assert "base2" == traces[1][0].resource

    def test_trace_map_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet(_):
            with self.tracer.trace("greenlet", resource="base"):
                gevent.sleep(0.01)

        funcs = [
            gevent.pool.Group().map,
            gevent.pool.Group().imap,
            gevent.pool.Group().imap_unordered,
            gevent.pool.Pool(2).map,
            gevent.pool.Pool(2).imap,
            gevent.pool.Pool(2).imap_unordered,
        ]
        for func in funcs:
            with self.tracer.trace("outer", resource="base"):
                # Use a list to force evaluation
                list(func(greenlet, [0, 1, 2]))
            traces = self.pop_traces()

            assert len(traces) == 1
            spans = traces[0]
            outer_span = [s for s in spans if s.name == "outer"][0]

            assert "base" == outer_span.resource
            inner_spans = [s for s in spans if s is not outer_span]
            for s in inner_spans:
                assert "greenlet" == s.name
                assert "base" == s.resource
                assert outer_span.trace_id == s.trace_id
                assert outer_span.span_id == s.parent_id

    def test_trace_later_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.trace("greenlet") as span:
                span.resource = "base"

        gevent.spawn_later(0.01, greenlet).join()
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        assert "greenlet" == traces[0][0].name
        assert "base" == traces[0][0].resource

    def test_trace_sampling_priority_spawn_multiple_greenlets_multiple_traces(self):
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.tracer.trace("greenlet.main") as span:
                span.context.sampling_priority = USER_KEEP
                span.resource = "base"
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace("greenlet.worker") as span:
                span.set_tag("worker_id", "1")
                gevent.sleep(0.01)

        def green_2():
            with self.tracer.trace("greenlet.worker") as span:
                span.set_tag("worker_id", "2")
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 3 == len(traces[0])
        spans = traces[0]
        assert 3 == len(spans)
        parent_span = spans[0]
        worker_1 = spans[1]
        worker_2 = spans[2]
        # check sampling priority
        assert parent_span.get_metric(_SAMPLING_PRIORITY_KEY) == USER_KEEP
        assert worker_1.get_metric(_SAMPLING_PRIORITY_KEY) is None
        assert worker_2.get_metric(_SAMPLING_PRIORITY_KEY) is None

    def test_trace_spawn_multiple_greenlets_multiple_traces(self):
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.tracer.trace("greenlet.main") as span:
                span.resource = "base"
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace("greenlet.worker") as span:
                span.set_tag("worker_id", "1")
                gevent.sleep(0.01)

        def green_2():
            with self.tracer.trace("greenlet.worker") as span:
                span.set_tag("worker_id", "2")
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 3 == len(traces[0])
        parent_span = traces[0][0]
        worker_1 = traces[0][1]
        worker_2 = traces[0][2]
        # check spans data and hierarchy
        assert parent_span.name == "greenlet.main"
        assert parent_span.resource == "base"
        assert worker_1.get_tag("worker_id") == "1"
        assert worker_1.name == "greenlet.worker"
        assert worker_1.resource == "greenlet.worker"
        assert worker_1.parent_id == parent_span.span_id
        assert worker_2.get_tag("worker_id") == "2"
        assert worker_2.name == "greenlet.worker"
        assert worker_2.resource == "greenlet.worker"
        assert worker_2.parent_id == parent_span.span_id

    def test_trace_spawn_later_multiple_greenlets_multiple_traces(self):
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.tracer.trace("greenlet.main") as span:
                span.resource = "base"
                jobs = [gevent.spawn_later(0.01, green_1), gevent.spawn_later(0.01, green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace("greenlet.worker") as span:
                span.set_tag("worker_id", "1")
                gevent.sleep(0.01)

        def green_2():
            with self.tracer.trace("greenlet.worker") as span:
                span.set_tag("worker_id", "2")
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 3 == len(traces[0])
        parent_span = traces[0][0]
        worker_1 = traces[0][1]
        worker_2 = traces[0][2]
        # check spans data and hierarchy
        assert parent_span.name == "greenlet.main"
        assert parent_span.resource == "base"
        assert worker_1.get_tag("worker_id") == "1"
        assert worker_1.name == "greenlet.worker"
        assert worker_1.resource == "greenlet.worker"
        assert worker_1.parent_id == parent_span.span_id
        assert worker_2.get_tag("worker_id") == "2"
        assert worker_2.name == "greenlet.worker"
        assert worker_2.resource == "greenlet.worker"
        assert worker_2.parent_id == parent_span.span_id

    def test_trace_concurrent_calls(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one
        def greenlet():
            with self.tracer.trace("greenlet"):
                gevent.sleep(0.01)

        jobs = [gevent.spawn(greenlet) for x in range(100)]
        gevent.joinall(jobs)

        traces = self.pop_traces()
        assert 100 == len(traces)
        assert 1 == len(traces[0])
        assert "greenlet" == traces[0][0].name

    def test_propagation_with_new_context(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one
        ctx = Context(trace_id=100, span_id=101)
        self.tracer.context_provider.activate(ctx)

        def greenlet():
            with self.tracer.trace("greenlet"):
                gevent.sleep(0.01)

        jobs = [gevent.spawn(greenlet) for x in range(1)]
        gevent.joinall(jobs)

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        assert traces[0][0].trace_id == 100
        assert traces[0][0].parent_id == 101

    def test_trace_concurrent_spawn_later_calls(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one, even if greenlets
        # are delayed
        def greenlet():
            with self.tracer.trace("greenlet"):
                gevent.sleep(0.01)

        jobs = [gevent.spawn_later(0.01, greenlet) for x in range(100)]
        gevent.joinall(jobs)

        traces = self.pop_traces()
        assert 100 == len(traces)
        assert 1 == len(traces[0])
        assert "greenlet" == traces[0][0].name

    @silence_errors
    def test_exception(self):
        # it should catch the exception like usual
        def greenlet():
            with self.tracer.trace("greenlet"):
                raise Exception("Custom exception")

        g = gevent.spawn(greenlet)
        g.join()
        assert isinstance(g.exception, Exception)

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]
        assert 1 == span.error
        assert "Custom exception" == span.get_tag(ERROR_MSG)
        assert "Traceback (most recent call last)" in span.get_tag("error.stack")

    def _assert_spawn_multiple_greenlets(self, spans):
        """A helper to assert the parenting of a trace when greenlets are
        spawned within another greenlet.

        This is meant to help maintain compatibility between the Datadog and
        OpenTracing tracer implementations.

        Note that for gevent there is differing behaviour between the context
        management so the traces are not identical in form. However, the
        parenting of the spans must remain the same.
        """
        assert len(spans) == 3

        parent = None
        worker_1 = None
        worker_2 = None
        # get the spans since they can be in any order
        for span in spans:
            if span.name == "greenlet.main":
                parent = span
            if span.name == "greenlet.worker1":
                worker_1 = span
            if span.name == "greenlet.worker2":
                worker_2 = span
        assert parent
        assert worker_1
        assert worker_2

        # confirm the parenting
        assert worker_1.parent_id == parent.span_id
        assert worker_2.parent_id == parent.span_id

        # check spans data and hierarchy
        assert parent.name == "greenlet.main"
        assert worker_1.get_tag("worker_id") == "1"
        assert worker_1.name == "greenlet.worker1"
        assert worker_1.resource == "greenlet.worker1"
        assert worker_2.get_tag("worker_id") == "2"
        assert worker_2.name == "greenlet.worker2"
        assert worker_2.resource == "greenlet.worker2"

    def test_trace_spawn_multiple_greenlets_multiple_traces_dd(self):
        """Datadog version of the same test."""

        def entrypoint():
            with self.tracer.trace("greenlet.main") as span:
                span.resource = "base"
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace("greenlet.worker1") as span:
                span.set_tag("worker_id", "1")
                gevent.sleep(0.01)

        # note that replacing the `tracer.trace` call here with the
        # OpenTracing equivalent will cause the checks to fail
        def green_2():
            with self.tracer.trace("greenlet.worker2") as span:
                span.set_tag("worker_id", "2")
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        spans = self.pop_spans()
        self._assert_spawn_multiple_greenlets(spans)

    def test_ddtracerun(self):
        """
        Regression test case for the following issue.

        ddtrace-run imports all available modules in order to patch them.
        However, gevent depends on the ssl module not being imported when it
        goes to monkeypatch. Modules that import ssl include botocore, requests
        and elasticsearch.
        """

        # Ensure modules are installed
        import aiobotocore  # noqa:F401
        import aiohttp  # noqa:F401
        import botocore  # noqa:F401
        import elasticsearch  # noqa:F401
        import opensearchpy  # noqa:F401
        import pynamodb  # noqa:F401
        import requests  # noqa:F401

        p = subprocess.Popen(
            ["ddtrace-run", "python", "tests/contrib/gevent/monkeypatch.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        stdout, stderr = p.communicate()
        assert p.returncode == 0, f"stdout: {stdout.decode()}\n\nstderr: {stderr.decode()}"
        assert b"Test success" in stdout, stdout.decode()
        assert b"RecursionError" not in stderr, stderr.decode()
