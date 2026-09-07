from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import ClassVar
from typing import MutableMapping
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import MESSAGING_BATCH_COUNT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_MESSAGE_ID
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.core.events import event_field


class MessagingEvents(str, Enum):
    PRODUCE = "messaging.produce"
    PROCESS = "messaging.process"


@dataclass
class MessagingEvent(TracingEvent):
    """Shared tracing data for messaging operations."""

    operation: str = event_field()
    system: str = event_field()
    destination: Optional[str] = event_field(default=None)
    message_id: Optional[str] = event_field(default=None)
    batch_count: Optional[int] = event_field(default=None)
    distributed_headers: Optional[MutableMapping[str, Any]] = event_field(default=None)
    messaging_operation: ClassVar[str]

    def __post_init__(self) -> None:
        self.operation_name = self.operation
        self.tags.setdefault(MESSAGING_SYSTEM, self.system)
        self.tags.setdefault(MESSAGING_OPERATION, self.messaging_operation)
        if self.destination is not None:
            self.tags.setdefault(MESSAGING_DESTINATION_NAME, self.destination)
        if self.message_id is not None:
            self.tags.setdefault(MESSAGING_MESSAGE_ID, self.message_id)
        if self.batch_count is not None:
            self.tags.setdefault(MESSAGING_BATCH_COUNT, str(self.batch_count))


@dataclass
class MessagingProducerEvent(MessagingEvent):
    event_name = MessagingEvents.PRODUCE.value
    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER
    messaging_operation = "send"


@dataclass
class MessagingProcessEvent(MessagingEvent):
    event_name = MessagingEvents.PROCESS.value
    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER
    messaging_operation = "process"

    activate_distributed_headers: bool = event_field(default=True)
    failed: bool = event_field(default=False)
    result_tags: dict[str, Any] = event_field(default_factory=dict)
