from typing import List
from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings._env import get_env as _get_env


class APMTracingEnabledFilter(TraceProcessor):
    """
    Trace processor that drops all APM traces when DD_APM_TRACING_ENABLED is set to a falsy value.
    """

    def __init__(self) -> None:
        super().__init__()
        self._apm_tracing_enabled = asbool(_get_env("DD_APM_TRACING_ENABLED", "true"))

    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        if not self._apm_tracing_enabled:
            return None
        return trace
