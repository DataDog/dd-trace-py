"""Request lifecycle notifications emitted inside an existing integration scope.

WebFrameworkRequestEvent configures a context_with_event scope: its subscribers
create and finish the request span. These dispatch-only events mark analysis
boundaries using an integration's existing context and span, whether its scope
uses context_with_event or context_with_data. Products start after span creation
and finish before span closure, without relying on subscriber registration order.
"""

from dataclasses import dataclass

from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import SpanData


@dataclass
class WebRequestEvent(Event):
    """Carry the existing request context and span; do not create another scope.

    Each lifecycle notification must use the same context and span instances as
    Ready. A nested integration borrowing the request must not complete its owner.
    """

    ctx: ExecutionContext[Event]
    span: SpanData


@dataclass
class WebRequestReady(WebRequestEvent):
    """The span is bound and request analysis can start, before application code runs."""

    event_name = "web.request.ready"


@dataclass
class WebRequestCompleting(WebRequestEvent):
    """Finalize request analysis and attach results before the request span finishes.

    Emit on successful and exceptional completion at the integration's response
    boundary. This can run in a response task or callback. Request analysis must
    be finished when this event returns. Cover setup failures and cancellation
    with a finally block; early span-finish paths must emit before finishing.
    A lexical scope exit alone is not completion for a deferred response iterator.
    """

    event_name = "web.request.completing"
