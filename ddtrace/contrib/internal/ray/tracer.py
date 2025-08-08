import logging
import os

from ddtrace import config
import ddtrace.auto  # noqa: F401
from ddtrace.constants import _DJM_ENABLED_KEY
from ddtrace.constants import _FILTER_KEPT_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.opentelemetry import TracerProvider
from ddtrace.trace import TraceFilter
from ddtrace.trace import tracer
from opentelemetry.trace import set_tracer_provider
from pythonjsonlogger import jsonlogger


RAY_JOB_ID_TAG_KEY = "ray.job_id"
DEFAULT_SERVICE_NAME = "unspecified-ray-job"
DEFAULT_SPAN_NAME = "ray.job"

FORMAT = ('%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] '
              '[dd.service=%(dd.service)s dd.env=%(dd.env)s '
              'dd.version=%(dd.version)s '
              'dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] '
              '- %(message)s')

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
                # span.ray_job_submission_id = None  # TODO: Add job submission id

        return trace


class CustomJsonFormatter(jsonlogger.JsonFormatter):  # pyright: ignore[reportPrivateImportUsage]
        def add_fields(self, log_record, record, message_dict):
            super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)

            # log_record['ray_job_submission_id'] = None  # TODO: Add job submission id


def setup_tracing() -> None:
    tracer.configure(trace_processors=[RayTraceFilter()])
    set_tracer_provider(TracerProvider())

    for name in logging.root.manager.loggerDict.keys():
        logger = logging.getLogger(name)
        if name.startswith("ray"):
            stream_handler = logging.StreamHandler()
            json_formatter = CustomJsonFormatter(format=FORMAT)
            stream_handler.setFormatter(json_formatter)
            logger.addHandler(stream_handler)
