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

    operation_name: str = event_field()

    distributed_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)


@dataclass
class MessagingConsumeEvent(TracingEvent):
    event_name = MessagingEvents.CONSUME.value
    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER

    operation_name: str = event_field()

    request_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)
    span_links: list["Context"] = event_field(default_factory=list)
