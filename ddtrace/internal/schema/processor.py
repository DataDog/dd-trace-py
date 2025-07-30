from ddtrace._trace.processor import TraceProcessor
from ddtrace.constants import _BASE_SERVICE_KEY
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.settings._config import config

from . import schematize_service_name


class BaseServiceProcessor(TraceProcessor):
    def __init__(self):
        self._global_service = schematize_service_name((config.service or "").lower())
        self._in_aws_lambda = in_aws_lambda()

    def process_trace(self, trace):
        # AWS Lambda spans receive unhelpful base_service value of runtime
        # Remove base_service to prevent service overrides in Lambda spans
        if not trace or self._in_aws_lambda:
            return trace

        traces_to_process = filter(
            lambda x: x.service and x.service.lower() != self._global_service,
            trace,
        )
        any(map(lambda x: self._update_dd_base_service(x), traces_to_process))

        return trace

    def _update_dd_base_service(self, span):
        span.set_tag_str(key=_BASE_SERVICE_KEY, value=self._global_service)
