import gevent
from opentracing.scope_managers.gevent import GeventScopeManager
import pytest

import ddtrace
from ddtrace.contrib.gevent import patch
from ddtrace.contrib.gevent import unpatch


@pytest.fixture()
def ot_tracer(ot_tracer_factory):
    """Fixture providing an opentracer configured for gevent usage."""
    # patch gevent
    patch()
    yield ot_tracer_factory("gevent_svc", {}, GeventScopeManager(), ddtrace.contrib.gevent.context_provider)
    # unpatch gevent
    unpatch()


class TestTracerGevent(object):
    """Converted Gevent tests for the regular tracer.

    Ensures that greenlets are properly traced when using
    the opentracer.
    """

    def test_no_threading(self, ot_tracer):
        with ot_tracer.start_span("span") as span:
            span.set_tag("tag", "value")

        assert span.finished

    def test_greenlets(self, ot_tracer, test_spans):
        def f():
            with ot_tracer.start_span("f") as span:
                gevent.sleep(0.04)
                span.set_tag("f", "yes")

        def g():
            with ot_tracer.start_span("g") as span:
                gevent.sleep(0.03)
                span.set_tag("g", "yes")

        with ot_tracer.start_active_span("root"):
            gevent.joinall([gevent.spawn(f), gevent.spawn(g)])

        traces = test_spans.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 3

    def test_trace_greenlet(self, ot_tracer, test_spans):
        # a greenlet can be traced using the trace API
        def greenlet():
            with ot_tracer.start_span("greenlet"):
                pass

        gevent.spawn(greenlet).join()
        traces = test_spans.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        assert traces[0][0].name == "greenlet"

    def test_trace_later_greenlet(self, ot_tracer, test_spans):
        # a greenlet can be traced using the trace API
        def greenlet():
            with ot_tracer.start_span("greenlet"):
                pass

        gevent.spawn_later(0.01, greenlet).join()
        traces = test_spans.pop_traces()

        assert len(traces) == 1
        assert len(traces[0]) == 1
        assert traces[0][0].name == "greenlet"

    def test_trace_concurrent_calls(self, ot_tracer, test_spans):
        # create multiple futures so that we expect multiple
        # traces instead of a single one
        def greenlet():
            with ot_tracer.start_span("greenlet"):
                gevent.sleep(0.01)

        jobs = [gevent.spawn(greenlet) for x in range(100)]
        gevent.joinall(jobs)

        traces = test_spans.pop_traces()

        assert len(traces) == 100
        assert len(traces[0]) == 1
        assert traces[0][0].name == "greenlet"

    def test_trace_concurrent_spawn_later_calls(self, ot_tracer, test_spans):
        # create multiple futures so that we expect multiple
        # traces instead of a single one, even if greenlets
        # are delayed
        def greenlet():
            with ot_tracer.start_span("greenlet"):
                gevent.sleep(0.01)

        jobs = [gevent.spawn_later(0.01, greenlet) for x in range(100)]
        gevent.joinall(jobs)

        traces = test_spans.pop_traces()
        assert len(traces) == 100
        assert len(traces[0]) == 1
        assert traces[0][0].name == "greenlet"


class TestTracerGeventCompatibility(object):
    """Ensure the opentracer works in tandem with the ddtracer and gevent."""

    def test_trace_spawn_multiple_greenlets_multiple_traces_ot_parent(self, ot_tracer, dd_tracer, test_spans):
        """
        Copy of gevent test with the same name but testing with mixed usage of
        the opentracer and datadog tracers.

        Uses an opentracer span as the parent span.
        """

        # multiple greenlets must be part of the same trace
        def entrypoint():
            with ot_tracer.start_active_span("greenlet.main"):
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with dd_tracer.trace("greenlet.worker") as span:
                span.set_tag("worker_id", "1")
                gevent.sleep(0.01)

        def green_2():
            with ot_tracer.start_span("greenlet.worker") as span:
                span.set_tag("worker_id", "2")
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = test_spans.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 3
        parent_span = traces[0][0]
        worker_1 = traces[0][1]
        worker_2 = traces[0][2]
        # check spans data and hierarchy
        assert parent_span.name == "greenlet.main"
        assert worker_1.get_tag("worker_id") == "1"
        assert worker_1.name == "greenlet.worker"
        assert worker_1.resource == "greenlet.worker"
        assert worker_1.parent_id == parent_span.span_id
        assert worker_2.get_tag("worker_id") == "2"
        assert worker_2.name == "greenlet.worker"
        assert worker_2.resource == "greenlet.worker"
        assert worker_2.parent_id == parent_span.span_id

    def test_trace_spawn_multiple_greenlets_multiple_traces_dd_parent(self, ot_tracer, dd_tracer, test_spans):
        """
        Copy of gevent test with the same name but testing with mixed usage of
        the opentracer and datadog tracers.

        Uses an opentracer span as the parent span.
        """

        # multiple greenlets must be part of the same trace
        def entrypoint():
            with dd_tracer.trace("greenlet.main"):
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with ot_tracer.start_span("greenlet.worker") as span:
                span.set_tag("worker_id", "1")
                gevent.sleep(0.01)

        def green_2():
            with dd_tracer.trace("greenlet.worker") as span:
                span.set_tag("worker_id", "2")
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = test_spans.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 3
        parent_span = traces[0][0]
        worker_1 = traces[0][1]
        worker_2 = traces[0][2]
        # check spans data and hierarchy
        assert parent_span.name == "greenlet.main"
        assert worker_1.get_tag("worker_id") == "1"
        assert worker_1.name == "greenlet.worker"
        assert worker_1.resource == "greenlet.worker"
        assert worker_1.parent_id == parent_span.span_id
        assert worker_2.get_tag("worker_id") == "2"
        assert worker_2.name == "greenlet.worker"
        assert worker_2.resource == "greenlet.worker"
        assert worker_2.parent_id == parent_span.span_id
