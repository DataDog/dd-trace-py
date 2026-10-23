"""Inbound callback observations using the shared, tracing-free HTTP vocabulary."""

from dataclasses import dataclass
from typing import Optional

from ddtrace.contrib._events.http import HttpBaseEvent
from ddtrace.contrib._events.http import _HttpResponse
from ddtrace.internal.core.events import event_field


@dataclass
class WebFrameworkCallbackEvent(HttpBaseEvent):
    """Owned callback metadata, separate from tracing configuration.

    Treat envelopes as read-only. Mapping fields contain copied header values;
    response_header_items preserves duplicates that a mapping cannot represent.
    callback_id identifies this invocation, including nested calls.
    """

    callback_id: object = event_field()
    framework: str = event_field()
    request_route: str = event_field()
    query: str = event_field()
    response_header_items: tuple[tuple[str, str], ...] = event_field(default=())

    def __post_init__(self) -> None:
        self.request_headers = dict(self.request_headers)
        if self.response_header_items:
            self.response_header_items = tuple(self.response_header_items)
            self.response_headers = dict(self.response_header_items)
        else:
            self.response_headers = dict(self.response_headers)
            self.response_header_items = tuple(self.response_headers.items())

    def set_response(self, response: _HttpResponse) -> None:
        super().set_response(response)
        self.response_headers = dict(self.response_headers)
        self.response_header_items = tuple(self.response_headers.items())


@dataclass
class WebFrameworkCallbackStartedEvent(WebFrameworkCallbackEvent):
    """A plugin callback boundary, independent of tracing and transport completion."""

    event_name = "web.framework.callback.started"


@dataclass
class WebFrameworkCallbackCompletedEvent(WebFrameworkCallbackEvent):
    """The callback boundary exited, before response iteration and transport delivery.

    Completion is dispatched before the associated tracing context exits, if any.
    Errors are descriptions and retain neither exception objects nor tracebacks.
    """

    event_name = "web.framework.callback.completed"

    error_type: Optional[str] = event_field(default=None)
    error_message: Optional[str] = event_field(default=None)
