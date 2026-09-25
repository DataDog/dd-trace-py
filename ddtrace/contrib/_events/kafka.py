from dataclasses import dataclass
from typing import Any
from typing import Optional

from ddtrace.contrib._events.messaging import MessagingConsumeEvent
from ddtrace.contrib._events.messaging import MessagingEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.internal.core.events import event_field


@dataclass
class KafkaEvent(MessagingEvent):
    """Raw Kafka request data. MessagingTracingSubscriber derives tags from it."""

    topic: Optional[str] = event_field(default=None)
    bootstrap_servers: Any = event_field(default=None)
    cluster_id: Optional[str] = event_field(default=None)
    message_key: Any = event_field(default=None)
    partition: Optional[int] = event_field(default=None)
    tombstone: Optional[bool] = event_field(default=None)
    message_offset: Optional[int] = event_field(default=None)


@dataclass
class KafkaProducerEvent(MessagingProducerEvent, KafkaEvent):
    # Own event name (rather than inheriting messaging.produce) so KafkaTracingSubscriber
    # can register independently, without double-running span start/finish alongside
    # MessagingTracingSubscriber.
    event_name = "kafka.produce"


@dataclass
class KafkaConsumeEvent(MessagingConsumeEvent, KafkaEvent):
    # See KafkaProducerEvent.event_name.
    event_name = "kafka.consume"

    group_id: Optional[str] = event_field(default=None)
    received_message: Optional[bool] = event_field(default=None)
