from dataclasses import InitVar
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from typing import Any
from typing import MutableMapping
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


if TYPE_CHECKING:
    from ddtrace._trace.context import Context


class MessagingEvents(str, Enum):
    PRODUCE = "messaging.produce"
    CONSUME = "messaging.consume"


@dataclass
class MessagingProducerEvent(TracingEvent):
    event_name = MessagingEvents.PRODUCE.value
    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER

    messaging_operation: InitVar[str] = event_field()
    provider: InitVar[str] = event_field()

    def __post_init__(self, messaging_operation: str, provider: str) -> None:
        self.operation_name = schematize_messaging_operation(  # type: ignore[operator]  # Dynamic schema function.
            messaging_operation, provider=provider, direction=SpanDirection.OUTBOUND
        )

    distributed_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)


@dataclass
class MessagingConsumeEvent(TracingEvent):
    event_name = MessagingEvents.CONSUME.value
    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER

    request_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)
    span_links: list["Context"] = event_field(default_factory=list)

    messaging_operation: InitVar[str] = event_field()
    provider: InitVar[str] = event_field()
    direction: InitVar[SpanDirection] = event_field(default=SpanDirection.PROCESSING)

    def __post_init__(self, messaging_operation: str, provider: str, direction: SpanDirection) -> None:
        self.operation_name = schematize_messaging_operation(  # type: ignore[operator]  # Dynamic schema function.
            messaging_operation, provider=provider, direction=direction
        )
