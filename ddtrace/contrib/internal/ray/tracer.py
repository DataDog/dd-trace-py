from ddtrace import config
import ddtrace.auto  # noqa: F401
from ddtrace.constants import _DJM_ENABLED_KEY
from ddtrace.constants import _FILTER_KEPT_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.trace import TraceFilter
from ddtrace.trace import tracer


RAY_JOB_ID_TAG_KEY = "ray.job_id"
DEFAULT_SERVICE_NAME = "unspecified-ray-job"
DEFAULT_SPAN_NAME = "ray.job"


class RayTraceFilter(TraceFilter):
    def process_trace(self, trace):
        for span in trace:
            if span.get_tag(RAY_JOB_ID_TAG_KEY) is not None:
                span.span_type = f"ray.{span.name}"
                span.name = DEFAULT_SPAN_NAME
                span.service = config.service or DEFAULT_SERVICE_NAME
                span.set_metric(_DJM_ENABLED_KEY, 1)
                span.set_metric(_FILTER_KEPT_KEY, 1)
                span.set_metric(_SPAN_MEASURED_KEY, 1)
                span.set_metric(_SAMPLING_PRIORITY_KEY, 2)
        return trace


def setup_tracing() -> None:
    tracer.configure(trace_processors=[RayTraceFilter()])
