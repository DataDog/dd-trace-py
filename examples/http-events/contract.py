"""Experimental HTTP consumer API; register before requests begin.

Treat events as read-only. None means unavailable; empty values mean known empty.
Raise HttpRequestBlock only synchronously from a block-check listener. The first
block ends that check; response delivery and completion remain adapter-owned.
"""

from collections.abc import Callable

from ddtrace.contrib._events.http import HttpRequestBlock
from ddtrace.contrib._events.http import HttpRequestBlockCheckEvent
from ddtrace.contrib._events.http import HttpRequestCompletedEvent
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRouteResolvedEvent
from ddtrace.internal import core


def register_listeners(
    record_endpoint: Callable[[HttpRouteResolvedEvent], None],
    record_outcome: Callable[[HttpRequestCompletedEvent], None],
    should_block: Callable[[HttpRequestData], bool],
) -> Callable[[], None]:
    # One-way listeners read event.request.route, event.response, and event.errors.
    # event.request_id correlates phases without exposing tracing state.
    def check_request(event: HttpRequestBlockCheckEvent) -> None:
        if should_block(event.request):
            raise HttpRequestBlock(
                status_code=403,
                headers=(("content-type", "text/plain"),),
                body=b"Request denied",
            )

    core.on(HttpRouteResolvedEvent.event_name, record_endpoint)
    core.on(HttpRequestCompletedEvent.event_name, record_outcome)
    core.on(HttpRequestBlockCheckEvent.event_name, check_request)

    def unregister() -> None:
        core.reset_listeners(HttpRouteResolvedEvent.event_name, record_endpoint)
        core.reset_listeners(HttpRequestCompletedEvent.event_name, record_outcome)
        core.reset_listeners(HttpRequestBlockCheckEvent.event_name, check_request)

    return unregister
