"""Request notifications using an integration's existing context and span.

Use the same context and span for both notifications. Unlike WebFrameworkRequestEvent,
these events neither open a scope nor manage its span.
"""

from dataclasses import dataclass
from enum import Enum

from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import SpanData


class WebRequestEvents(str, Enum):
    WEB_REQUEST_READY = "web.request.ready"
    WEB_REQUEST_COMPLETING = "web.request.completing"


@dataclass
class WebRequestReady(Event):
    """The request span is bound in its active context, before application code runs.

    Span modifiers and HTTP metadata setup may still run afterward.
    """

    event_name = WebRequestEvents.WEB_REQUEST_READY.value

    ctx: ExecutionContext[Event]
    span: SpanData


@dataclass
class WebRequestCompleting(Event):
    """The request span is about to finish, from response sending or teardown.

    This includes errors and cancellation; queued parents wait for their children.
    Completion may run in another task, so use ctx to access the request's state.
    Application work and final-tag hooks may still run afterward. Nested wrappers
    must leave the enclosing request's analysis running.
    """

    event_name = WebRequestEvents.WEB_REQUEST_COMPLETING.value

    ctx: ExecutionContext[Event]
    span: SpanData
