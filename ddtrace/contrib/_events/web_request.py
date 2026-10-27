"""Request notifications using an integration's existing context and span.

Use the same context and span for both notifications. Unlike WebFrameworkRequestEvent,
these events neither open a scope nor manage its span.
"""

from dataclasses import dataclass

from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import SpanData


@dataclass
class WebRequestReady(Event):
    """The span is bound and request analysis can start, before application code runs."""

    event_name = "web.request.ready"

    ctx: ExecutionContext[Event]
    span: SpanData


@dataclass
class WebRequestCompleting(Event):
    """Attach analysis before the span finishes, including on errors and cancellation.

    Completion may run in another task or callback. A nested wrapper must not
    finalize an enclosing request.
    """

    event_name = "web.request.completing"

    ctx: ExecutionContext[Event]
    span: SpanData
