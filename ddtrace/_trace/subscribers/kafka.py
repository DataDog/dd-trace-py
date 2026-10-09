from types import TracebackType
from typing import Any
from typing import Optional

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers.messaging import MessagingConsumeSubscriber
from ddtrace._trace.subscribers.messaging import MessagingProduceSubscriber
from ddtrace.contrib._events.kafka import KafkaConsumeEvent
from ddtrace.contrib._events.kafka import KafkaProducerEvent
from ddtrace.ext import kafka as kafkax
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.span_bus import span_from_context
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


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
    topics_partitions: Optional[dict[str, list[int]]] = None,
) -> None:
    """Set Kafka metas on the span from raw KafkaEvent data.

    Called from KafkaProduceSubscriber/KafkaConsumeSubscriber.on_ended.
    """
    span._set_attribute(MESSAGING_SYSTEM, kafkax.SERVICE)

    if topic is not None:
        span._set_attribute(kafkax.TOPIC, topic)
        if topic:
            span._set_attribute(MESSAGING_DESTINATION_NAME, topic)

    if topics_partitions:
        span._set_attribute(MESSAGING_DESTINATION_NAME, next(iter(topics_partitions)))
        span._set_attribute(kafkax.TOPIC, ",".join(topics_partitions))
        for message_topic, partitions in topics_partitions.items():
            span._set_attribute(f"kafka.partitions.{message_topic}", ",".join(map(str, sorted(partitions))))

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


class KafkaProduceSubscriber(MessagingProduceSubscriber):
    event_names = (KafkaProducerEvent.event_name,)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[Any],
        _exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: KafkaProducerEvent = ctx.event
        set_kafka_meta(
            span_from_context(ctx),
            cluster_id=event.cluster_id,
            topic=event.topic,
            bootstrap_servers=event.bootstrap_servers,
            message_key=event.message_key,
            partition=event.partition,
            tombstone=event.tombstone,
            message_offset=event.message_offset,
        )


class KafkaConsumeSubscriber(MessagingConsumeSubscriber):
    event_names = (KafkaConsumeEvent.event_name,)

    @staticmethod
    def _extract_context(headers: dict[str, Any]) -> Context:
        # HTTPPropagator.extract has no return annotation in the legacy propagation API.
        return HTTPPropagator.extract(  # type: ignore[no-untyped-call, no-any-return]
            {
                key: (value.decode("utf-8", errors="ignore") if isinstance(value, (bytes, bytearray)) else str(value))
                for key, value in headers.items()
                if value is not None
            }
        )

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext[Any]) -> None:
        event: KafkaConsumeEvent = ctx.event
        # Choose the producer parent before span creation.
        if (
            event.integration_config.distributed_tracing_enabled
            and not event.propagation_as_span_links
            and event.message_headers
        ):
            parent = cls._extract_context(event.message_headers[0])
            if parent.trace_id and parent.span_id:
                event.distributed_context = parent
                event.use_active_context = False
        if event.activate and event.distributed_context is not None:
            event.previous_active_context = tracer.context_provider.active()
        super()._on_context_started(ctx)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[Any],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: KafkaConsumeEvent = ctx.event
        if event.integration_config.distributed_tracing_enabled and event.propagation_as_span_links:
            event.span_links.extend(cls._extract_context(headers) for headers in event.message_headers)
        try:
            super()._on_context_ended(ctx, exc_info)
        finally:
            # A remote parent cannot restore the local span displaced by activation.
            if event.activate and event.distributed_context is not None:
                tracer.context_provider.activate(event.previous_active_context)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[Any],
        _exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: KafkaConsumeEvent = ctx.event
        span = span_from_context(ctx)
        set_kafka_meta(
            span,
            cluster_id=event.cluster_id,
            topic=event.topic,
            bootstrap_servers=event.bootstrap_servers,
            message_key=event.message_key,
            partition=event.partition,
            tombstone=event.tombstone,
            message_offset=event.message_offset,
            group_id=event.group_id,
            received_message=event.received_message,
            topics_partitions=event.topics_partitions,
        )
        if event.error is not None:
            span.set_exc_info(type(event.error), event.error, event.error.__traceback__)
