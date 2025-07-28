from ddtrace._trace.span import Span
from ddtrace.contrib.internal.ray.tracer import RayTraceFilter


def test_trace_filter():
    trace = [Span("span0")]
    ray_trace_filter = RayTraceFilter()
    filtered_trace = ray_trace_filter.process_trace(trace)

    assert len(filtered_trace) == 1
    assert filtered_trace[0].span_type == "ray.span0"
    assert filtered_trace[0].name == "ray.job"
    assert filtered_trace[0].service == "unspecified-ray-job"
    assert filtered_trace[0].get_metric("_dd.djm.enabled") == 1
    assert filtered_trace[0].get_metric("_dd.filter.kept") == 1
    assert filtered_trace[0].get_metric("_dd.measured") == 1
    assert filtered_trace[0].get_metric("_sampling_priority_v1") == 2
