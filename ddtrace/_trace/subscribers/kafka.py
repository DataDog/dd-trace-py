from typing import Any
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.ext import kafka as kafkax
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM


def set_kafka_meta(
    span: Span,
    cluster_id: Optional[str] = None,
    topic: Optional[str] = None,
    bootstrap_servers: Optional[str] = None,
    message_key: Any = None,
    partition: Optional[int] = None,
    tombstone: Optional[bool] = None,
    message_offset: Optional[int] = None,
    group_id: Optional[str] = None,
    received_message: Optional[bool] = None,
) -> None:
    """Set Kafka metas on the span from raw KafkaEvent data.

    Called from KafkaProduceSubscriber/KafkaConsumeSubscriber.on_ended.
    """
    span._set_attribute(MESSAGING_SYSTEM, kafkax.SERVICE)

    if topic is not None:
        span._set_attribute(kafkax.TOPIC, topic)
        if topic:
            span._set_attribute(MESSAGING_DESTINATION_NAME, topic)

    if bootstrap_servers is not None:
        span._set_attribute(kafkax.HOST_LIST, bootstrap_servers)

    if cluster_id:
        span._set_attribute(kafkax.CLUSTER_ID, cluster_id)

    if message_key is not None:
        span._set_attribute(kafkax.MESSAGE_KEY, message_key)

    if partition is not None:
        span._set_attribute(kafkax.PARTITION, partition)

    if tombstone is not None:
        span._set_attribute(kafkax.TOMBSTONE, str(tombstone))

    if message_offset is not None:
        span._set_attribute(kafkax.MESSAGE_OFFSET, message_offset)

    if group_id is not None:
        span._set_attribute(kafkax.GROUP_ID, group_id)

    if received_message is not None:
        span._set_attribute(kafkax.RECEIVED_MESSAGE, str(received_message))
