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
    PROCESS = "messaging.process"


@dataclass
class MessagingEvent(TracingEvent):
    """Shared tracing data for messaging operations."""

    operation: str = event_field()
    distributed_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)

    def __post_init__(self) -> None:
        self.operation_name = self.operation


@dataclass
class MessagingProducerEvent(MessagingEvent):
    event_name = MessagingEvents.PRODUCE.value
    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER


@dataclass
class MessagingProcessEvent(MessagingEvent):
    event_name = MessagingEvents.PROCESS.value
    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER

    activate_distributed_headers: bool = event_field(default=True)
    failed: bool = event_field(default=False)
    result_tags: dict[str, Any] = event_field(default_factory=dict)
