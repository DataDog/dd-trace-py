from types import TracebackType
from typing import Optional
from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.subscribers.kafka import set_kafka_meta
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.kafka import KafkaConsumeEvent
from ddtrace.contrib._events.kafka import KafkaEvent
from ddtrace.contrib._events.messaging import MessagingConsumeEvent
from ddtrace.contrib._events.messaging import MessagingEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context
from ddtrace.propagation.http import HTTPPropagator


class MessagingTracingSubscriber(TracingSubscriber[MessagingEvent]):
    event_names = (
        MessagingProducerEvent.event_name,
        MessagingConsumeEvent.event_name,
    )

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[MessagingEvent]) -> None:
        event = ctx.event

        if (
            isinstance(event, MessagingProducerEvent)
            and event.distributed_headers is not None
            and trace_utils.distributed_tracing_enabled(event.integration_config)
        ):
            HTTPPropagator.inject(
                span_from_context(ctx).context,
                cast(dict[str, str], event.distributed_headers),
            )

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[MessagingEvent],
        _exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event = ctx.event
        span = span_from_context(ctx)

        if isinstance(event, KafkaEvent):
            set_kafka_meta(
                span,
                cluster_id=event.cluster_id,
                topic=event.topic,
                bootstrap_servers=event.bootstrap_servers,
                message_key=event.message_key,
                partition=event.partition,
                tombstone=event.tombstone,
                message_offset=event.message_offset,
                group_id=event.group_id if isinstance(event, KafkaConsumeEvent) else None,
                received_message=event.received_message if isinstance(event, KafkaConsumeEvent) else None,
            )

        span.set_tags(event.additional_tags)

        if not isinstance(event, MessagingConsumeEvent):
            return

        for link_ctx in event.span_links:
            if not link_ctx.trace_id or not link_ctx.span_id:
                continue
            span.link_span(link_ctx)
            for extracted_link in link_ctx._span_links:
                span.set_link(
                    trace_id=extracted_link.trace_id,
                    span_id=extracted_link.span_id,
                    tracestate=extracted_link.tracestate,
                    flags=extracted_link.flags,
                    attributes=extracted_link.attributes,
                )
