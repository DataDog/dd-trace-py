from typing import List

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span
from ddtrace.constants import _BASE_SERVICE_KEY
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.settings._config import config

from . import schematize_service_name


class BaseServiceProcessor(TraceProcessor):
    def __init__(self):
        self._global_service = str(schematize_service_name((config.service or "").lower()))
        self._in_aws_lambda = in_aws_lambda()

    def process_trace(self, trace: List[Span]):
        # AWS Lambda spans receive unhelpful base_service value of runtime
        # Remove base_service to prevent service overrides in Lambda spans
        if self._in_aws_lambda:
            return trace

        for span in trace:
            if not span.service or span.service == self._global_service:
                continue

            if span.service.lower() != self._global_service:
                span.set_tag_str(_BASE_SERVICE_KEY, self._global_service)

        return trace
