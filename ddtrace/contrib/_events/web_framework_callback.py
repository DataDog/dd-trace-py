"""Observation events for web framework handler callbacks.

Kept separate from web_framework.py so that subscribing to a callback boundary does
not require importing tracing. web_framework.py pulls in ddtrace._trace.events, the
span type/kind enums, and the span naming schema in order to declare
WebFrameworkRequestEvent; nothing here needs any of that. A listener that only wants
to observe handler invocations -- security, profiling, diagnostics -- should be able
to import this module with tracing disabled or absent.

Field names deliberately match the HTTP vocabulary in _events/http.py so consumers
read one set of names across client, server, and observation events. The type differences are
intentional: the HTTP events hand out live, mutable framework objects, while these
carry copies, because a listener must not be able to mutate the request or response
the framework is still using.
"""

from dataclasses import dataclass
from typing import Optional

from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


@dataclass
class WebFrameworkCallbackStartedEvent(Event):
    """A plugin callback boundary, independent of tracing and transport completion.

    Treat fields as read-only. Headers are copied; bodies and live framework objects
    are excluded. callback_id identifies this invocation, including nested calls.
    """

    event_name = "web.framework.callback.started"

    callback_id: object = event_field()
    framework: str = event_field()
    request_method: str = event_field()
    request_url: str = event_field()
    request_route: str = event_field()
    request_headers: tuple[tuple[str, str], ...] = event_field()
    query: str = event_field()


@dataclass
class WebFrameworkCallbackCompletedEvent(WebFrameworkCallbackStartedEvent):
    """The callback boundary exited, before response iteration and transport delivery.

    Completion is dispatched before the associated tracing context exits, if any.
    Errors are descriptions and retain neither exception objects nor tracebacks.
    """

    event_name = "web.framework.callback.completed"

    response_status_code: int = event_field()
    response_headers: tuple[tuple[str, str], ...] = event_field()
    error_type: Optional[str] = event_field(default=None)
    error_message: Optional[str] = event_field(default=None)
