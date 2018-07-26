import opentracing
import unittest

import ddtrace
from tests.opentracer.utils import opentracer_init


class TestTracerCompatibility(unittest.TestCase):
    """Ensure that our opentracer produces results in the underlying ddtracer."""

    def setUp(self):
        self.ot_tracer, self.dd_tracer = opentracer_init(set_global=False)
        self.writer = self.dd_tracer.writer

    def test_ot_dd_global_tracers(self):
        """Ensure our test function opentracer_init() prep"""
        ot_tracer, dd_tracer = opentracer_init(set_global=True)

        # check all the global references
        assert ot_tracer is opentracing.tracer
        assert ot_tracer._dd_tracer is dd_tracer
        assert dd_tracer is ddtrace.tracer

    def test_ot_dd_nested_trace(self):
        """Ensure intertwined usage of the opentracer and ddtracer."""

        with self.ot_tracer.start_span("my_ot_span") as otspan:
            with self.dd_tracer.trace("my_dd_span") as ddspan:
                pass
        spans = self.writer.pop()
        assert len(spans) == 2

        # confirm the ordering
        assert spans[0] is otspan._dd_span
        assert spans[1] is ddspan

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id == spans[0].span_id

    def test_dd_ot_nested_trace(self):
        """Ensure intertwined usage of the opentracer and ddtracer."""
        with self.dd_tracer.trace("my_dd_span") as ddspan:
            with self.ot_tracer.start_span("my_ot_span") as otspan:
                pass
        spans = self.writer.pop()
        assert len(spans) == 2

        # confirm the ordering
        assert spans[0] is ddspan
        assert spans[1] is otspan._dd_span

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id

    def test_ot_dd_ot_dd_nested_trace(self):
        """Ensure intertwined usage of the opentracer and ddtracer."""
        with self.ot_tracer.start_span("my_ot_span") as otspan:
            with self.dd_tracer.trace("my_dd_span") as ddspan:
                with self.ot_tracer.start_span("my_ot_span") as otspan2:
                    with self.dd_tracer.trace("my_dd_span") as ddspan2:
                        pass

        spans = self.writer.pop()
        assert len(spans) == 4

        # confirm the ordering
        assert spans[0] is otspan._dd_span
        assert spans[1] is ddspan
        assert spans[2] is otspan2._dd_span
        assert spans[3] is ddspan2

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].parent_id is spans[1].span_id
        assert spans[3].parent_id is spans[2].span_id

    def test_ot_ot_dd_ot_dd_nested_trace_active(self):
        """Ensure intertwined usage of the opentracer and ddtracer."""
        with self.ot_tracer.start_active_span("my_ot_span") as otscope:
            with self.ot_tracer.start_active_span("my_ot_span") as otscope2:
                with self.dd_tracer.trace("my_dd_span") as ddspan:
                    with self.ot_tracer.start_active_span("my_ot_span") as otscope3:
                        with self.dd_tracer.trace("my_dd_span") as ddspan2:
                            pass

        spans = self.writer.pop()
        assert len(spans) == 5

        # confirm the ordering
        assert spans[0] is otscope.span._dd_span
        assert spans[1] is otscope2.span._dd_span
        assert spans[2] is ddspan
        assert spans[3] is otscope3.span._dd_span
        assert spans[4] is ddspan2

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id == spans[0].span_id
        assert spans[2].parent_id == spans[1].span_id
        assert spans[3].parent_id == spans[2].span_id
        assert spans[4].parent_id == spans[3].span_id

    def test_consecutive_trace(self):
        """Ensure consecutive usage of the opentracer and ddtracer."""
        with self.ot_tracer.start_active_span("my_ot_span") as otscope:
            pass

        with self.dd_tracer.trace("my_dd_span") as ddspan:
            pass

        with self.ot_tracer.start_active_span("my_ot_span") as otscope2:
            pass

        with self.dd_tracer.trace("my_dd_span") as ddspan2:
            pass

        spans = self.writer.pop()
        assert len(spans) == 4

        # confirm the ordering
        assert spans[0] is otscope.span._dd_span
        assert spans[1] is ddspan
        assert spans[2] is otscope2.span._dd_span
        assert spans[3] is ddspan2

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id is None
        assert spans[2].parent_id is None
        assert spans[3].parent_id is None

    def test_ddtrace_wrapped_fn(self):
        """Ensure ddtrace wrapped functions work with the opentracer"""
        @self.dd_tracer.wrap()
        def fn():
            with self.ot_tracer.start_span("ot_span_inner"):
                pass

        with self.ot_tracer.start_active_span("ot_span_outer"):
            fn()

        spans = self.writer.pop()
        assert len(spans) == 3

        # confirm the ordering
        assert spans[0].name == "ot_span_outer"
        assert spans[1].name == "tests.opentracer.test_dd_compatibility.fn"
        assert spans[2].name == "ot_span_inner"

        # check the parenting
        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].parent_id is spans[1].span_id
