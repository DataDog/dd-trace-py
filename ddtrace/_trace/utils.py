from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import List
from typing import Optional


if TYPE_CHECKING:
    from ddtrace._trace.context import Context
    from ddtrace._trace.span import Span

from ddtrace.propagation.http import HTTPPropagator


def extract_DD_context_from_messages(messages: List[Any], extract_from_message: Callable) -> Optional["Context"]:
    ctx = None
    if len(messages) >= 1:
        message = messages[0]
        context_json = extract_from_message(message)
        if context_json is not None:
            child_of = HTTPPropagator.extract(context_json)
            if child_of.trace_id is not None:
                ctx = child_of
    return ctx


def is_span_type_web(span: "Span") -> bool:
    """Return True if span.span_type indicates this is a web request span."""
    if span.span_type in ("web", "http"):
        return True
    # OpenTracing and OpenTelemetry integrations may set span_type to None
    # In this case, we check for HTTP method in meta tags
    return span.get_tag("http.method") is not None
