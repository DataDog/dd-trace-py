from ddtrace._trace.processor import TraceProcessor
from ddtrace.constants import _BASE_SERVICE_KEY
from ddtrace.settings._config import config
from ddtrace.internal.serverless import in_aws_lambda, in_gcp_function, in_azure_function

from . import schematize_service_name


class BaseServiceProcessor(TraceProcessor):
    def __init__(self):
        # Determine the global (root) service for this process according to the
        # active schema.  In serverless environments the inferred base service
        # often resolves to the string ``"runtime"`` which is not useful to
        # users and pollutes span metadata.  Detect that situation once and, if
        # applicable, disable tagging entirely.

        self._global_service = schematize_service_name((config.service or "").lower())

        # Skip tagging when running in a serverless runtime *and* the inferred
        # service name is the generic "runtime" placeholder.
        self._skip_tagging = self._global_service == "runtime" and (
            in_aws_lambda() or in_gcp_function() or in_azure_function()
        )

    def process_trace(self, trace):
        if not trace or self._skip_tagging:
            # Nothing to do (either no spans, or tagging disabled for this env)
            return trace

        traces_to_process = filter(
            lambda x: x.service and x.service.lower() != self._global_service,
            trace,
        )
        any(map(lambda x: self._update_dd_base_service(x), traces_to_process))

        return trace

    def _update_dd_base_service(self, span):
        span.set_tag_str(key=_BASE_SERVICE_KEY, value=self._global_service)
