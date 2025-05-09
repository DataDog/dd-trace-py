from typing import Any
from typing import Dict
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import IAST
from ddtrace.internal import core


class IASTEnvironment:
    """
    an object of this class contains all iast data
    for a single request. It is bound to a single iast request context.
    It is contained into a ContextVar.
    """

    def __init__(self, span: Optional[Span] = None):
        self.span = span or core.get_span()

        self.request_enabled: bool = False
        self.iast_reporter: Optional[Any] = None
        self.iast_span_metrics: Dict[str, int] = {}
        self.iast_stack_trace_id: int = 0
        self.iast_stack_trace_reported: bool = False
        self.vulnerability_copy_global_limit: Dict[str, int] = {}
        self.vulnerabilities_request_limit: Dict[str, int] = {}
        self.vulnerability_budget: int = 0
        self.is_first_vulnerability: bool = True
        self.endpoint_method: str = ""
        self.endpoint_route: str = ""

    @property
    def endpoint_key(self) -> str:
        """
        Create a unique key for an endpoint based on HTTP method and route.

        Args:
            method (str): HTTP method (GET, POST, etc.)
            route (str): HTTP route/endpoint path

        Returns:
            str: Combined key as "METHOD:ROUTE"
        """
        return f"{self.endpoint_method}:{self.endpoint_route}"


def _get_iast_env() -> Optional[IASTEnvironment]:
    return core.get_item(IAST.REQUEST_CONTEXT_KEY)


def in_iast_env() -> bool:
    return core.get_item(IAST.REQUEST_CONTEXT_KEY) is not None
