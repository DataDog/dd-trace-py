from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import MutableMapping
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field


class MessagingEvents(str, Enum):
    PRODUCE = "messaging.produce"
    RECEIVE = "messaging.receive"
    PROCESS = "messaging.process"
    ACTION = "messaging.action"


@dataclass
class MessagingEvent(TracingEvent):
    """Shared tracing data for messaging operations."""

    operation: str = event_field()
    messaging_system: Optional[str] = event_field(default=None)
    semantic_operation: Optional[str] = event_field(default=None)
    destination: Optional[str] = event_field(default=None)
    propagation_as_span_links: bool = event_field(default=False)

    def __post_init__(self) -> None:
        self.operation_name = self.operation


@dataclass
class MessagingProducerEvent(MessagingEvent):
    event_name = MessagingEvents.PRODUCE.value
    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER

    distributed_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)


@dataclass
class MessagingProcessEvent(MessagingEvent):
    event_name = MessagingEvents.PROCESS.value
    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER

    request_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)
    activate_distributed_headers: bool = event_field(default=True)


@dataclass
class MessagingReceiveEvent(MessagingEvent):
    event_name = MessagingEvents.RECEIVE.value
    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.WORKER

    request_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)
    start_ns: Optional[int] = event_field(default=None)
    activate_distributed_headers: bool = event_field(default=True)


@dataclass
class MessagingActionEvent(MessagingEvent):
    event_name = MessagingEvents.ACTION.value
    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.WORKER

    action: str = event_field(default="")
