from typing import TYPE_CHECKING
from typing import Optional
from typing import Union

from ddtrace.appsec._iast._constants import IAST
from ddtrace.internal import core
from ddtrace.internal import span_bus
from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import SpanData


if TYPE_CHECKING:  # pragma: no cover - type checking only
    from ddtrace.appsec._iast.reporter import IastSpanReporter


def _report_span_for_request(span: Optional[SpanData]) -> Optional[SpanData]:
    if not isinstance(span, SpanData):
        return None
    entry = span._service_entry_span
    # Background requests can start under an already finished request span.
    return span if entry.finished else entry


class IASTEnvironment:
    """
    an object of this class contains all iast data
    for a single request. It is bound to a single iast request context.
    It is contained into a ContextVar.

    Immutable source objects are retained until request teardown so their addresses
    cannot be reused while source overrides preserve their secure marks.
    """

    def __init__(self, span: Optional[SpanData] = None, context_id: Optional[int] = None) -> None:
        # The request span owns completion; its service-entry span receives the results.
        self.span = span or span_bus.get_span()
        self.report_span = _report_span_for_request(self.span)
        self.context_id = context_id
        self.owns_taint_context = True
        self.completed = False

        self.iast_reporter: Optional[IastSpanReporter] = None
        self.iast_span_metrics: dict[str, int] = {}
        self.iast_hash_object_tracking: dict[int, bool] = {}
        self.iast_taint_source_objects: dict[int, Union[str, bytes]] = {}
        self.iast_stack_trace_reported: bool = False
        self.vulnerability_copy_global_limit: dict[str, int] = {}
        self.vulnerabilities_request_limit: dict[str, int] = {}
        self.vulnerability_budget: int = 0
        self.is_first_vulnerability: bool = True
        self.endpoint_method: str = ""
        self.endpoint_route: str = ""

    @property
    def endpoint_key(self) -> str:
        """
        Create a unique key for an endpoint based on HTTP method and route.

        Returns:
            str: Combined key as "METHOD:ROUTE"
        """
        return f"{self.endpoint_method}:{self.endpoint_route}"


def _get_iast_env(context: Optional[core.ExecutionContext[Event]] = None) -> Optional[IASTEnvironment]:
    return (context or core.current).find_item(IAST.REQUEST_CONTEXT_KEY)


def in_iast_env() -> bool:
    environment = _get_iast_env()
    return environment is not None and not environment.completed
