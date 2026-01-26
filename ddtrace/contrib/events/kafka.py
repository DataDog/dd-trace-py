from enum import Enum

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib.events.messaging import MessagingConsumeEvent
from ddtrace.contrib.events.messaging import MessagingProduceEvent
from ddtrace.ext.kafka import CLUSTER_ID
from ddtrace.ext.kafka import GROUP_ID
from ddtrace.ext.kafka import HOST_LIST
from ddtrace.ext.kafka import MESSAGE_KEY
from ddtrace.ext.kafka import MESSAGE_OFFSET
from ddtrace.ext.kafka import PARTITION
from ddtrace.ext.kafka import RECEIVED_MESSAGE
from ddtrace.ext.kafka import TOMBSTONE
from ddtrace.ext.kafka import TOPIC
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.propagation.http import HTTPPropagator


class KafkaMessagingEvents(Enum):
    KAFKA_MESSAGING_PRODUCE = "kafka.messaging.produce"
    KAFKA_MESSAGING_CONSUME = "kafka.messaging.consume"


class KafkaMessagingProduceEvent(MessagingProduceEvent):
    event_name = KafkaMessagingEvents.KAFKA_MESSAGING_PRODUCE.value

    def __new__(
        cls,
        config,
        operation,
        provider,
        topic,
        bootstrap_servers,
        messaging_system,
        cluster_id,
        message_key,
        value,
        partition,
        tombstone,
        headers,
        tags=None,
    ):
        event_data = super().__new__(cls, config, operation, provider, tags)

        instance_tags = {
            TOPIC: topic,
            MESSAGING_SYSTEM: messaging_system,
            HOST_LIST: bootstrap_servers,
            CLUSTER_ID: cluster_id,
            MESSAGE_KEY: message_key,
            PARTITION: partition,
            TOMBSTONE: tombstone,
        }
        if topic:
            instance_tags[MESSAGING_DESTINATION_NAME] = topic

        event_data["config"] = config
        event_data["headers"] = headers
        event_data["tags"].update(instance_tags)
        return event_data

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        span = ctx.span
        span.set_metric(_SPAN_MEASURED_KEY, 1)

        config = ctx.get_item("config")
        headers = ctx.get_item("headers")

        if config.distributed_tracing_enabled:
            # inject headers with Datadog tags:
            HTTPPropagator.inject(span.context, headers)


class KafkaMessagingConsumeEvent(MessagingConsumeEvent):
    event_name = KafkaMessagingEvents.KAFKA_MESSAGING_CONSUME.value

    def __new__(
        cls,
        config,
        operation,
        provider,
        messaging_system,
        cluster_id,
        group_id,
        topic,
        is_tombstone,
        message_offset,
        message_key,
        received_message,
        partition,
        start_ns,
        error,
        tags=None,
    ):
        event_data = super().__new__(cls, config, operation, provider, tags)

        instance_tags = {
            MESSAGING_SYSTEM: messaging_system,
            RECEIVED_MESSAGE: received_message,
            GROUP_ID: group_id,
            PARTITION: partition,
        }
        if topic:
            instance_tags[MESSAGING_DESTINATION_NAME] = topic
            instance_tags[TOPIC] = topic
        if is_tombstone is not None:
            instance_tags[TOMBSTONE] = str(is_tombstone)
        if message_offset:
            instance_tags[MESSAGE_OFFSET] = message_offset
        if cluster_id:
            instance_tags[CLUSTER_ID] = cluster_id
        if message_key:
            instance_tags[MESSAGE_KEY] = message_key

        event_data["start_ns"] = start_ns
        event_data["error"] = error
        event_data["tags"].update(instance_tags)
        return event_data

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        span = ctx.span
        span.set_metric(_SPAN_MEASURED_KEY, 1)
        span.start_ns = ctx.get_item("start_ns")

        error = ctx.get_item("error")

        if error is not None:
            span.set_exc_info(error)
