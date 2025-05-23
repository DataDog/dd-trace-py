from typing import Dict
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.internal import core


class IASTEnvironment:
    """
    an object of this class contains all asm data (waf and telemetry)
    for a single request. It is bound to a single asm request context.
    It is contained into a ContextVar.
    """

    def __init__(self, span: Optional[Span] = None):
        self.span = span or core.get_span()

        self.request_enabled: bool = False
        self.iast_reporter: Optional[IastSpanReporter] = None
        self.iast_span_metrics: Dict[str, int] = {}
        self.iast_stack_trace_reported: bool = False


def _get_iast_env() -> Optional[IASTEnvironment]:
    return core.get_item(IAST.REQUEST_CONTEXT_KEY)


def in_iast_env() -> bool:
    return core.get_item(IAST.REQUEST_CONTEXT_KEY) is not None
