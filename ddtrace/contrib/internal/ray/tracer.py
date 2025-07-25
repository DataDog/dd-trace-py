import os

import ddtrace.auto
from ddtrace.trace import TraceFilter
from ddtrace.trace import tracer


class RayTraceFilter(TraceFilter):
    def process_trace(self, trace):
        for span in trace:
            span.span_type = "ray." + span.name
            span.name = "ray.job"
            span.service=os.environ.get("DD_SERVICE", "unspecified-ray-job")
            span.set_metric("_dd.djm.enabled", 1)
            span.set_metric("_dd.filter.kept", 1)
            span.set_metric("_dd.measured", 1)
            span.set_metric("_sampling_priority_v1", 2)
        return trace


def setup_tracing() -> None:
    tracer.configure(trace_processors=[RayTraceFilter()])
