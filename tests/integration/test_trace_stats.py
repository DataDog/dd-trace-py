import functools

import pytest

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import http
from tests.integration.utils import AGENT_VERSION
from tests.utils import override_global_config


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@pytest.fixture
def stats_tracer(tracer):
    # Recreate tracer with stats enabled
    with override_global_config(dict(_trace_compute_stats=True)):
        tracer._recreate()
        yield tracer
        tracer.shutdown()


class consistent_end_trace(object):
    """
    This class wraps tracer.trace() in order to ensure that the span is finished with consistent end time and duration
    """

    def __init__(self, orig_trace_fn, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.orig_trace_fn = orig_trace_fn

    def __enter__(self):
        self.span = self.orig_trace_fn(*self.args, **self.kwargs)
        return self.span

    def __exit__(self, exc_type, exc_value, traceback):
        self.span.start_ns = 1
        self.span.finish(finish_time=1)


@pytest.fixture
def send_once_stats_tracer(stats_tracer):
    """
    This is a variation on the tracer that has the SpanStatsProcessor disabled until we leave the tracer context.
    """
    # Save the original trace method so we can restore it after the test
    original_trace = stats_tracer.trace
    stats_tracer.trace = functools.partial(consistent_end_trace, stats_tracer.trace)

    yield stats_tracer

    # Restore the original trace method
    stats_tracer.trace = original_trace


# Can't use a value between 0 and 1 since sampling is not deterministic.
@pytest.mark.parametrize("sample_rate", [1.0, 0.0])
@pytest.mark.snapshot()
def test_sampling_rate(stats_tracer, sample_rate):
    """Ensure traces are sent according to the sampling rate."""
    for _ in range(10):
        with stats_tracer.trace("operation"):
            pass


@pytest.mark.snapshot()
def test_stats_30(send_once_stats_tracer):
    for _ in range(30):
        with send_once_stats_tracer.trace("name", service="abc", resource="/users/list"):
            pass


@pytest.mark.snapshot()
def test_stats_errors(send_once_stats_tracer):
    for i in range(30):
        with send_once_stats_tracer.trace("name", service="abc", resource="/users/list") as span:
            if i % 2 == 0:
                span.error = 1


@pytest.mark.snapshot()
def test_stats_aggrs(send_once_stats_tracer):
    """
    When different span properties are set
        The stats are put into different aggregations
    """
    with send_once_stats_tracer.trace(name="op", service="my-svc", span_type="web", resource="/users/list"):
        pass

    # Synthetics
    with send_once_stats_tracer.trace(name="op", service="my-svc", span_type="web", resource="/users/list") as span:
        span.context.dd_origin = "synthetics"

    # HTTP status code
    with send_once_stats_tracer.trace(name="op", service="my-svc", span_type="web", resource="/users/list") as span:
        span._set_attribute(http.STATUS_CODE, 200)

    # Resource
    with send_once_stats_tracer.trace(name="op", service="my-svc", span_type="web", resource="/users/view"):
        pass

    # Service
    with send_once_stats_tracer.trace(name="op", service="diff-svc", span_type="web", resource="/users/list"):
        pass

    # Name
    with send_once_stats_tracer.trace(name="diff-op", service="my-svc", span_type="web", resource="/users/list"):
        pass

    # Type
    with send_once_stats_tracer.trace(name="diff-op", service="my-svc", span_type="db", resource="/users/list"):
        pass


@pytest.mark.snapshot()
def test_measured_span(send_once_stats_tracer):
    for _ in range(10):
        with send_once_stats_tracer.trace("parent"):  # Should have stats
            with send_once_stats_tracer.trace("child"):  # Shouldn't have stats
                pass
    for _ in range(10):
        with send_once_stats_tracer.trace("parent"):  # Should have stats
            with send_once_stats_tracer.trace("child_stats") as span:  # Should have stats
                span._set_attribute(_SPAN_MEASURED_KEY, 1)


@pytest.mark.snapshot()
def test_top_level(send_once_stats_tracer):
    for _ in range(30):
        with send_once_stats_tracer.trace("parent", service="svc-one"):  # Should have stats
            with send_once_stats_tracer.trace("child", service="svc-two"):  # Should have stats
                pass


@pytest.mark.subprocess(
    env={
        "DD_TRACE_STATS_COMPUTATION_ENABLED": "true",
        "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0, "service":"test"}, {"sample_rate":1, "service":"test"}]',
    }
)
def test_single_span_sampling():
    from ddtrace import tracer

    with tracer.trace("parent", service="test"):
        with tracer.trace("child") as child:
            # FIXME: Replace with span sampling rule
            child._set_attribute("_dd.span_sampling.mechanism", 8)
