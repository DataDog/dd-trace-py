import pytest

from ddtrace.constants import ORIGIN_KEY
from ddtrace.ext.ci import CI_APP_TEST_ORIGIN
from tests.utils import DummyTracer


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    return tracer


def test_tracer_context(benchmark, tracer):
    def func(tracer):
        with tracer.trace("a", service="s", resource="r", span_type="t"):
            pass

    benchmark(func, tracer)


def test_tracer_wrap_staticmethod(benchmark, tracer):
    class Foo(object):
        @staticmethod
        @tracer.wrap()
        def func():
            return 0

    f = Foo()
    benchmark(f.func)


def test_tracer_wrap_classmethod(benchmark, tracer):
    class Foo(object):
        @classmethod
        @tracer.wrap()
        def func(cls):
            return 0

    f = Foo()
    benchmark(f.func)


def test_tracer_wrap_instancemethod(benchmark, tracer):
    class Foo(object):
        @tracer.wrap()
        def func(self):
            return 0

    f = Foo()
    benchmark(f.func)


def test_tracer_start_finish_span(benchmark, tracer):
    def func(tracer):
        s = tracer.start_span("benchmark")
        s.finish()

    benchmark(func, tracer)


def test_trace_simple_trace(benchmark, tracer):
    def func(tracer):
        with tracer.trace("parent"):
            for i in range(5):
                with tracer.trace("child") as c:
                    c.set_tag("i", i)

    benchmark(func, tracer)


def test_tracer_large_trace(benchmark, tracer):
    import random

    # generate trace with 1024 spans
    @tracer.wrap()
    def func(tracer, level=0):
        span = tracer.current_span()

        # do some work
        num = random.randint(1, 10)
        span.set_tag("num", num)

        if level < 10:
            func(tracer, level + 1)
            func(tracer, level + 1)

    benchmark(func, tracer)


def test_tracer_start_span(benchmark, tracer):
    benchmark(tracer.start_span, "benchmark")


def dd_origin_tracing_scenario(tracer, num_spans):
    """Tracing scenario (includes writing and encoding) for CIApp dd_origin propagation"""
    with tracer.trace("pytest-test") as span:
        span.context.dd_origin = CI_APP_TEST_ORIGIN
        for _ in range(num_spans - 1):
            with tracer.trace(""):
                pass


@pytest.mark.benchmark(group="encoding.dd_origin", min_time=0.005)
def test_dd_origin_tagging_with_processor_1_span(benchmark):
    """Propagate dd_origin tags to all spans in 1-span trace via TraceTagsProcessor"""
    tracer = DummyTracer()
    # TraceTagsProcessor.process_trace() should be called on every span on_span_finish()
    benchmark(dd_origin_tracing_scenario, tracer, 1)

    spans = tracer.writer.pop()
    for span in spans:
        assert span.meta[ORIGIN_KEY] == CI_APP_TEST_ORIGIN


@pytest.mark.benchmark(group="encoding.dd_origin", min_time=0.005)
def test_dd_origin_tagging_with_processor_50_spans(benchmark):
    """Propagate dd_origin tags to all spans in 50-span trace via TraceTagsProcessor"""
    tracer = DummyTracer()
    # TraceTagsProcessor.process_trace() should be called on every span on_span_finish()
    benchmark(dd_origin_tracing_scenario, tracer, 50)

    spans = tracer.writer.pop()
    for span in spans:
        assert span.meta[ORIGIN_KEY] == CI_APP_TEST_ORIGIN


@pytest.mark.benchmark(group="encoding.dd_origin", min_time=0.005)
def test_dd_origin_tagging_with_processor_200_spans(benchmark):
    """Propagate dd_origin tags to all spans in 200-span trace via TraceTagsProcessor"""
    tracer = DummyTracer()
    # TraceTagsProcessor.process_trace() should be called on every span on_span_finish()
    benchmark(dd_origin_tracing_scenario, tracer, 200)

    spans = tracer.writer.pop()
    for span in spans:
        assert span.meta[ORIGIN_KEY] == CI_APP_TEST_ORIGIN


@pytest.mark.benchmark(group="encoding.dd_origin", min_time=0.005)
def test_dd_origin_tagging_with_processor_2000_spans(benchmark):
    """Propagate dd_origin tags to all spans in 2000-span trace via TraceTagsProcessor"""
    tracer = DummyTracer()
    # TraceTagsProcessor.process_trace() should be called on every span on_span_finish()
    benchmark(dd_origin_tracing_scenario, tracer, 2000)

    spans = tracer.writer.pop()
    for span in spans:
        assert span.meta[ORIGIN_KEY] == CI_APP_TEST_ORIGIN
