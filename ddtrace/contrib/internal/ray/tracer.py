import contextvars
import json
import logging
import os

from ddtrace._trace.processor import SpanProcessor


os.environ["DD_TRACE_OTEL_ENABLED"] = "True"

from pythonjsonlogger import jsonlogger

from ddtrace import config
import ddtrace.auto  # noqa: F401
from ddtrace.constants import _DJM_ENABLED_KEY
from ddtrace.constants import _FILTER_KEPT_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.trace import TraceFilter, Span
from ddtrace.trace import tracer


RAY_PREFIX = "ray."
RAY_JOB_ID_TAG_KEY = "ray.job_id"
DEFAULT_SERVICE_NAME = "unspecified-ray-job"
DEFAULT_SPAN_NAME = "ray.job"
DEFAULT_OUTPUT_LOG_DIR = "/tmp/ray/session_latest/logs/"
OUTPUT_LOG_FILENAME = "ddtracepy.log"

FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] "
    "[dd.service=%(dd.service)s dd.env=%(dd.env)s "
    "dd.version=%(dd.version)s "
    "- %(message)s"
)

dd_ray_trace_id = contextvars.ContextVar("dd_ray_trace_id")
dd_ray_span_id = contextvars.ContextVar("dd_ray_span_id")
dd_ray_parent_id = contextvars.ContextVar("dd_ray_parent_id")
dd_ray_job_submission_id = contextvars.ContextVar("dd_ray_job_submission_id")


def get_job_submission_id():
    try:
        ray_env_str = os.environ.get("RAY_JOB_CONFIG_JSON_ENV_VAR", "")
        ray_env = json.loads(ray_env_str)
        job_submission_id = ray_env.get("metadata").get("job_submission_id")
        return job_submission_id
    except (json.decoder.JSONDecodeError, KeyError, AttributeError) as e:
        return None
    
def on_start_span(span: Span):
    dd_ray_span_id.set(span.span_id)
    dd_ray_trace_id.set(span.trace_id)
    if span.parent_id == 0 and span.name != "ray.job.submit":
        span.parent_id = dd_ray_parent_id.get()
    
# class RaySpanProcessor(SpanProcessor):
#     def on_span_start(self, span):
#         dd_ray_span_id.set(span.span_id)
#         dd_ray_trace_id.set(span.trace_id)
#         dd_ray_parent_id.set(span.parent_id)
#         return super().on_span_start(span)
#     def on_span_finish(self, span):
#         dd_ray_span_id.set(None)
#         dd_ray_trace_id.set(None)
#         dd_ray_parent_id.set(None)
#         return super().on_span_finish(span)

class RayTraceFilter(TraceFilter):
    def process_trace(self, trace):
        for span in trace:
            if span.get_tag(RAY_JOB_ID_TAG_KEY) is not None:
                span.span_type = f"{RAY_PREFIX}{span.name}"
                span.name = DEFAULT_SPAN_NAME
                span.service = config.service or DEFAULT_SERVICE_NAME
                span.set_metric(_DJM_ENABLED_KEY, 1)
                span.set_metric(_FILTER_KEPT_KEY, 1)
                span.set_metric(_SPAN_MEASURED_KEY, 1)
                span.set_metric(_SAMPLING_PRIORITY_KEY, 2)

                span.set_tag("job_name", dd_ray_job_submission_id.get())
                span.set_tag("operation_name", "ray.job")

        return trace
    
class ContextAwareJsonFormatter(jsonlogger.JsonFormatter): # type: ignore
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        
        # print(f"Log record trace_id={dd_ray_trace_id.get()}, span_id = {dd_ray_span_id.get()}, job_submission_id={dd_ray_job_submission_id.get()}")
        
        log_record['dd.trace_id'] = dd_ray_trace_id.get()
        log_record['dd.span_id'] = dd_ray_span_id.get()
        log_record['ray.job_submission_id'] = dd_ray_job_submission_id.get()


def setup_tracing() -> None:

    tracer.configure(trace_processors=[RayTraceFilter()])

    tracer.on_start_span(on_start_span)

    with tracer.trace("ray.job.setup_tracing") as span:
        span.parent_id = 0
        dd_ray_span_id.set(span.span_id)
        dd_ray_parent_id.set(span.span_id)
        dd_ray_trace_id.set(span.trace_id)

    # ray_span_processor = RaySpanProcessor()
    # tracer._span_processors.append(ray_span_processor)

    job_submission_id = get_job_submission_id()
    if job_submission_id is not None:
        dd_ray_job_submission_id.set(job_submission_id)

    for name in logging.root.manager.loggerDict.keys():
        logger = logging.getLogger(name)
        if name.startswith(RAY_PREFIX):
            json_formatter = ContextAwareJsonFormatter(fmt=FORMAT)  # type: ignore
            out_log_dir = os.environ.get("DD_RAY_TMP_DIR", DEFAULT_OUTPUT_LOG_DIR)
            os.makedirs(out_log_dir, exist_ok=True)
            out_log_path = os.path.join(out_log_dir, OUTPUT_LOG_FILENAME)            
            file_handler = logging.FileHandler(out_log_path)
            file_handler.setFormatter(json_formatter)
            logger.addHandler(file_handler)
