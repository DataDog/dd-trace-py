from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib.events.messaging import MessagingProduceEvent
from ddtrace.ext.kafka import CLUSTER_ID
from ddtrace.ext.kafka import HOST_LIST
from ddtrace.ext.kafka import MESSAGE_KEY
from ddtrace.ext.kafka import PARTITION
from ddtrace.ext.kafka import TOMBSTONE
from ddtrace.ext.kafka import TOPIC
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.propagation.http import HTTPPropagator


class KafkaMessagingProduceEvent(MessagingProduceEvent):
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
            TOMBSTONE: str(value is None),
        }
        if topic:
            instance_tags[MESSAGING_DESTINATION_NAME] = topic

        event_data["kafka_topic"] = topic
        event_data["kafka_cluster_id"] = cluster_id
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

        if config.kafka.distributed_tracing_enabled:
            # inject headers with Datadog tags:
            HTTPPropagator.inject(span.context, headers)
