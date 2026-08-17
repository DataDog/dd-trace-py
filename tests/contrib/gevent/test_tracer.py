import subprocess  # noqa:I001
import threading
from typing import Optional

import gevent
import gevent.pool
from greenlet import getcurrent
import pytest

from ddtrace._trace.provider import ActiveTrace
from ddtrace._trace.provider import BaseContextProvider
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.gevent.patch import patch
from ddtrace.contrib.internal.gevent.patch import unpatch
from ddtrace.trace import Context
from tests.utils import TracerTestCase

from .utils import silence_errors


class _GreenletContextProvider(BaseContextProvider):
    def __init__(self) -> None:
        super().__init__()
        self._contexts: dict[object, Optional[ActiveTrace]] = {}
        self.activation_count = 0

    def _has_active_context(self) -> bool:
        return self.active() is not None

    def activate(self, ctx: Optional[ActiveTrace]) -> None:
        self.activation_count += 1
        self._contexts[getcurrent()] = ctx
        super().activate(ctx)

    def active(self) -> Optional[ActiveTrace]:
        return self._contexts.get(getcurrent())


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

    def test_greenlet_propagation_uses_configured_context_provider(self) -> None:
        """Propagate context through the tracer's configured provider."""
        original_provider = self.tracer.context_provider
        configured_provider = _GreenletContextProvider()
        parent_context = Context(trace_id=100, span_id=101)
        self.tracer.configure(context_provider=configured_provider)
        configured_provider.activate(parent_context)

        try:
            propagated_context = gevent.spawn(self.tracer.context_provider.active).get()

            assert propagated_context is parent_context, "The greenlet did not receive the parent context"
            assert configured_provider.activation_count == 2, (
                "Expected one parent activation and one greenlet activation; "
                "context switches must not activate the provider"
            )
        finally:
            configured_provider.activate(None)
            self.tracer.configure(context_provider=original_provider)

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


@pytest.mark.subprocess()
def test_context_switches_publish_in_multiple_native_thread_hubs() -> None:
    """Publish late-listener context from every native-thread hub."""
    import concurrent.futures
    import threading
    from typing import Optional

    import gevent

    from ddtrace.internal import core
    from ddtrace.trace import Context
    from ddtrace.trace import tracer
    from tests.contrib.gevent.utils import gevent_patched

    workers_ready = threading.Barrier(2)
    active_span_ids_by_thread: dict[int, Optional[int]] = {}

    def record_active_span_on_context_switch() -> None:
        active = tracer.context_provider.active()
        active_span_ids_by_thread[threading.get_ident()] = active.span_id if active is not None else None

    def switch_greenlet_in_native_thread(span_id: int) -> tuple[int, Optional[int]]:
        native_thread_id = threading.get_ident()
        tracer.context_provider.activate(Context(trace_id=span_id, span_id=span_id))
        try:
            hub_id = id(gevent.get_hub())
            workers_ready.wait(timeout=10)
            gevent.spawn(gevent.sleep, 0).get()
            return hub_id, active_span_ids_by_thread.get(native_thread_id)
        finally:
            tracer.context_provider.activate(None)

    with gevent_patched(force_context_switch=True):
        core.on("python.context.switch", record_active_span_on_context_switch)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                thread_results = list(executor.map(switch_greenlet_in_native_thread, (1, 2)))
        finally:
            core.reset_listeners("python.context.switch", record_active_span_on_context_switch)

    hub_ids, observed_span_ids = zip(*thread_results)
    assert len(set(hub_ids)) == 2, f"Expected two native-thread hubs, got {hub_ids}"
    assert list(observed_span_ids) == [1, 2], (
        f"Expected each hub to publish its thread's active span, got {observed_span_ids}"
    )


@pytest.mark.subprocess()
def test_unpatch_restores_trace_callback_in_other_native_thread() -> None:
    """Restore a native thread's previous callback after another thread unpatches."""
    import concurrent.futures
    import threading

    import gevent
    from greenlet import gettrace
    from greenlet import greenlet
    from greenlet import settrace

    from ddtrace.contrib.internal.gevent.patch import unpatch
    from tests.contrib.gevent.utils import gevent_patched

    worker_watcher_ready = threading.Barrier(2)
    unpatch_complete = threading.Event()
    customer_trace_events: list[str] = []

    def customer_trace(event: str, args: object) -> None:
        customer_trace_events.append(event)

    def run_watched_greenlet_in_native_thread() -> bool:
        previous_trace = settrace(customer_trace)
        try:
            gevent.spawn(lambda: None).get()
            worker_watcher_ready.wait(timeout=10)
            assert unpatch_complete.wait(timeout=10), "The main thread did not complete gevent unpatching"

            # The next switch lets the disabled watcher restore this thread's callback.
            greenlet(lambda: None).switch()
            return gettrace() is customer_trace
        finally:
            settrace(previous_trace)

    try:
        with gevent_patched(force_context_switch=True):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                worker_future = executor.submit(run_watched_greenlet_in_native_thread)
                worker_watcher_ready.wait(timeout=10)
                unpatch()
                unpatch_complete.set()
                customer_trace_restored = worker_future.result(timeout=10)
    finally:
        unpatch_complete.set()

    assert customer_trace_events, "The watcher did not chain the existing trace callback"
    assert customer_trace_restored, "Unpatch left the Datadog watcher installed in the worker thread"
