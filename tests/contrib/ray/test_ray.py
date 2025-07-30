from ddtrace._trace.span import Span
from ddtrace.constants import _DJM_ENABLED_KEY
from ddtrace.constants import _FILTER_KEPT_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib.internal.ray.tracer import DEFAULT_SPAN_NAME
from ddtrace.contrib.internal.ray.tracer import RAY_JOB_ID_TAG_KEY
from ddtrace.contrib.internal.ray.tracer import RayTraceFilter


def test_trace_filter_detects_ray_spans():
    span = Span("span0")
    span.set_tag(RAY_JOB_ID_TAG_KEY, "01000000")
    trace = [span]
    ray_trace_filter = RayTraceFilter()
    filtered_trace = ray_trace_filter.process_trace(trace)

    assert len(filtered_trace) == 1
    assert filtered_trace[0].name == DEFAULT_SPAN_NAME
    assert filtered_trace[0].span_type == "ray.span0"
    assert filtered_trace[0].get_metric(_DJM_ENABLED_KEY) == 1
    assert filtered_trace[0].get_metric(_FILTER_KEPT_KEY) == 1
    assert filtered_trace[0].get_metric(_SPAN_MEASURED_KEY) == 1
    assert filtered_trace[0].get_metric(_SAMPLING_PRIORITY_KEY) == 2


def test_trace_filter_skips_non_ray_spans():
    span = Span("span0")
    trace = [span]
    ray_trace_filter = RayTraceFilter()
    filtered_trace = ray_trace_filter.process_trace(trace)

    assert len(filtered_trace) == 1
    assert filtered_trace[0].name == "span0"
    assert filtered_trace[0].span_type is None
    assert filtered_trace[0].get_metric(_DJM_ENABLED_KEY) is None
    assert filtered_trace[0].get_metric(_FILTER_KEPT_KEY) is None
    assert filtered_trace[0].get_metric(_SPAN_MEASURED_KEY) is None
    assert filtered_trace[0].get_metric(_SAMPLING_PRIORITY_KEY) is None
