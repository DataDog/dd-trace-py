"""
Messaging events for message broker tracing.

MessagingEvent is the base for all messaging integrations (Kafka, RabbitMQ, SQS, etc.).
KafkaEvent extends MessagingEvent with Kafka-specific fields.
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Callable
from typing import Dict
from typing import Optional

from ddtrace._trace.integrations.events.base._base import NetworkingEvent


@dataclass
class MessagingEvent(NetworkingEvent):
    """
    Event for messaging operations (Kafka, RabbitMQ, SQS, etc.).
    """

    _span_type: str = "worker"
    instrumentation_category: str = "messaging"

    # Messaging identification
    messaging_system: Optional[str] = None
    messaging_destination_name: Optional[str] = None
    messaging_operation: Optional[str] = None  # publish, receive, process
    messaging_message_id: Optional[str] = None

    # Batch info
    messaging_batch_message_count: Optional[int] = None

    # Internal use (not tags)
    _propagation_headers: Dict[str, str] = field(default_factory=dict)
    _inject_headers_callback: Optional[Callable[[Dict[str, str]], None]] = None


@dataclass
class KafkaEvent(MessagingEvent):
    """
    Kafka-specific event extending MessagingEvent.
    """

    messaging_system: str = "kafka"

    # Kafka-specific tags (field name = tag name)
    messaging_kafka_partition: Optional[int] = None
    messaging_kafka_offset: Optional[int] = None
    messaging_kafka_message_key: Optional[str] = None
    messaging_kafka_consumer_group: Optional[str] = None
    messaging_kafka_tombstone: Optional[bool] = None
