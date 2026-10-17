from collections.abc import Mapping
from dataclasses import dataclass

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import SpanDirection
from ddtrace.internal.schema import schematize_url_operation


@dataclass
class BottleRequestEvent(TracingEvent):
    """What is known when a Bottle request starts. Nothing writes to it afterwards.

    Scoping a span to the request needs a context, so this event exists to open one.
    Everything learned later -- route, status, headers, errors -- reaches consumers as
    a fresh HTTP observation event with frozen values rather than a mutation of this event, so a
    consumer never has to reason about when a field became trustworthy.
    """

    event_name = "bottle.request"

    span_type = SpanTypes.WEB
    span_kind = SpanKind.SERVER

    # Read once at span start to extract distributed tracing headers.
    request_headers: Mapping[str, str] = event_field(default_factory=dict)

    def __post_init__(self) -> None:
        # The schema implementation is selected from a heterogeneous function table.
        self.operation_name = schematize_url_operation(  # type: ignore[operator]
            "bottle.request", protocol="http", direction=SpanDirection.INBOUND
        )
