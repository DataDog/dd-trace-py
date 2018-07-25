import opentracing
import ddtrace
from tests.opentracer.utils import opentracer_init


class TestTracerCompatibility(object):
    """Ensure that our opentracer produces results in the underlying ddtracer."""

    def test_ot_dd_global_tracers(self):
        """Ensure our test function opentracer_init() prep"""
        ot_tracer, dd_tracer = opentracer_init()

        assert ot_tracer is opentracing.tracer
        assert ot_tracer._dd_tracer is dd_tracer
        assert dd_tracer is ddtrace.tracer

    def test_ot_dd_nested_trace(self):
        """Ensure intertwined usage of the opentracer and ddtracer."""
        ot_tracer, dd_tracer = opentracer_init()

        writer = dd_tracer.writer

        with opentracing.tracer.start_span("my_ot_span"):
            with dd_tracer.trace("my_dd_span"):
                pass
        spans = writer.pop()
        assert len(spans) == 2

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id == spans[0].span_id

    def test_dd_ot_nested_trace(self):
        """Ensure intertwined usage of the opentracer and ddtracer."""
        ot_tracer, dd_tracer = opentracer_init()
        writer = dd_tracer.writer

        with dd_tracer.trace("my_dd_span"):
            with opentracing.tracer.start_span("my_ot_span"):
                pass
        spans = writer.pop()
        assert len(spans) == 2

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id

    def test_ot_dd_ot_dd_nested_trace(self):
        """Ensure intertwined usage of the opentracer and ddtracer."""
        ot_tracer, dd_tracer = opentracer_init()
        writer = dd_tracer.writer

        with opentracing.tracer.start_span("my_ot_span"):
            with dd_tracer.trace("my_dd_span"):
                with opentracing.tracer.start_span("my_ot_span"):
                    with dd_tracer.trace("my_dd_span"):
                        pass

        spans = writer.pop()
        assert len(spans) == 4

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].parent_id is spans[1].span_id
        assert spans[3].parent_id is spans[2].span_id

    def test_ot_ot_dd_ot_dd_nested_trace_active(self):
        """Ensure intertwined usage of the opentracer and ddtracer."""
        ot_tracer, dd_tracer = opentracer_init()
        writer = dd_tracer.writer

        with opentracing.tracer.start_active_span("my_ot_span"):
            with opentracing.tracer.start_active_span("my_ot_span"):
                with dd_tracer.trace("my_dd_span"):
                    with opentracing.tracer.start_active_span("my_ot_span"):
                        with dd_tracer.trace("my_dd_span"):
                            pass

        spans = writer.pop()
        assert len(spans) == 5

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id == spans[0].span_id
        assert spans[2].parent_id == spans[1].span_id
        assert spans[3].parent_id == spans[2].span_id
        assert spans[4].parent_id == spans[3].span_id

    def test_consecutive_trace(self):
        """Ensure consecutive usage of the opentracer and ddtracer."""
        ot_tracer, dd_tracer = opentracer_init()
        writer = dd_tracer.writer

        with opentracing.tracer.start_active_span("my_ot_span"):
            pass

        with ddtrace.tracer.trace("my_dd_span"):
            pass

        with opentracing.tracer.start_active_span("my_ot_span"):
            pass

        with ddtrace.tracer.trace("my_dd_span"):
            pass

        spans = writer.pop()
        assert len(spans) == 4

        assert spans[0].parent_id is None
        assert spans[1].parent_id is None
        assert spans[2].parent_id is None
        assert spans[3].parent_id is None

    def test_ddtrace_wrapped_fn(self):
        """Ensure ddtrace wrapped functions work with the opentracer"""
        ot_tracer, dd_tracer = opentracer_init()
        writer = dd_tracer.writer

        @ddtrace.tracer.wrap()
        def fn():
            with opentracing.tracer.start_span("ot_span"):
                pass

        with opentracing.tracer.start_active_span("ot_span"):
            fn()

        spans = writer.pop()
        assert len(spans) == 3
        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].parent_id is spans[1].span_id
