import typing as t
from typing import NamedTuple

from ddtrace._trace.span import Span
from ddtrace.internal.constants import COLLECTOR_MAX_SIZE_PER_SPAN
from ddtrace.internal.constants import SPAN_EVENTS_HAS_EXCEPTION


class SpanEventData(NamedTuple):
    name: str
    attributes: dict[str, t.Any]
    time_unix_nano: t.Optional[int] = None


_span_exception_events: dict[int, dict[int, tuple[Exception, SpanEventData]]] = {}


def add_span_events(span: Span) -> None:
    """
    If the same error is handled/rethrown multiple times, report only one span event.
    Store handled exceptions until the span finishes so they can be deduplicated.
    """
    exception_data = get_exception_events(span.span_id).values()
    events = [event for _exc, event in exception_data]
    if events:
        span._set_attribute(SPAN_EVENTS_HAS_EXCEPTION, "true")
        for event in events:
            span._add_event(event.name, event.attributes, event.time_unix_nano)
    clear_exception_events(span.span_id)


def on_span_exception(span: Span, _exc_msg: object, exc_val: BaseException, _exc_tb: object) -> None:
    exception_events = get_exception_events(span.span_id)
    exc_id = id(exc_val)
    if exception_events and exc_id in exception_events:
        del exception_events[exc_id]


def capture_exception_event(span: Span, exc: Exception, event: SpanEventData) -> None:
    span_id = span.span_id
    events_dict = _span_exception_events.setdefault(span_id, {})
    if not events_dict:
        span._add_on_finish_exception_callback(add_span_events)
    exc_id = id(exc)
    if exc_id in events_dict or len(events_dict) < COLLECTOR_MAX_SIZE_PER_SPAN:
        # Store both exception and event to keep exception alive and prevent ID reuse.
        events_dict[exc_id] = (exc, event)


def get_exception_events(span_id: int) -> dict[int, tuple[Exception, SpanEventData]]:
    return _span_exception_events.get(span_id, {})


def clear_exception_events(span_id: int) -> None:
    _span_exception_events.pop(span_id, None)
