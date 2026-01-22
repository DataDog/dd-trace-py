from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.events.base import BaseEvent
from ddtrace.contrib.events.base import ContextEvent
from ddtrace.contrib.events.base import FinishEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext.kafka import CONSUME
from ddtrace.ext.kafka import GROUP_ID
from ddtrace.ext.kafka import HOST_LIST
from ddtrace.ext.kafka import MESSAGE_KEY
from ddtrace.ext.kafka import PARTITION
from ddtrace.ext.kafka import PRODUCE
from ddtrace.ext.kafka import SERVICE
from ddtrace.ext.kafka import TOMBSTONE
from ddtrace.ext.kafka import TOPIC
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator


class MessagingEvents(Enum):
    """Generic messaging event names for any messaging integration."""

    PRODUCE = "messaging.produce"
    PRODUCE_START = "messaging.produce.start"
    PRODUCE_COMPLETED = "messaging.produce.completed"
    CONSUME = "messaging.consume"
    CONSUME_START = "messaging.consume.start"
    CONSUME_COMPLETED = "messaging.consume.completed"
    CONSUME_BATCH = "messaging.consume.batch"
    CONSUME_BATCH_START = "messaging.consume.batch.start"
    CONSUME_BATCH_COMPLETED = "messaging.consume.batch.completed"
    COMMIT = "messaging.commit"


@dataclass
class MessagingProduceEvent(ContextEvent):
    def __init__(
        self,
        topic: str,
        bootstrap_servers: str,
        integration_config: Any,
        value: Any,
        key: Any,
        partition: Any,
        headers: List[Tuple[str, bytes]],
    ):
        self.event_name = MessagingEvents.PRODUCE.value
        self.span_type = SpanTypes.WORKER
        self.span_name = schematize_messaging_operation(PRODUCE, provider="kafka", direction=SpanDirection.OUTBOUND)
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.integration_config = integration_config
        self.value = value
        self.key = key
        self.partition = partition
        self.headers = headers
        self.tags = {
            COMPONENT: integration_config.integration_name,
            TOPIC: topic,
            MESSAGING_DESTINATION_NAME: topic,
            MESSAGING_SYSTEM: SERVICE,
            HOST_LIST: bootstrap_servers,
        }
        self.service = trace_utils.ext_service(None, integration_config)
        self._context_end = False  # Don't auto-finish span, use MessagingProduceFinishedEvent

        super().__post_init__()

    def on_context_started(self, ctx: core.ExecutionContext) -> None:
        span = ctx.span

        span._metrics[_SPAN_MEASURED_KEY] = 1
        span._set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        span._set_tag_str(TOMBSTONE, str(self.value is None))
        span.set_tag(MESSAGE_KEY, self.key.decode("utf-8") if self.key else None)
        span.set_metric(PARTITION, self.partition or -1)

        if self.integration_config.distributed_tracing_enabled:
            tracing_headers: Dict[str, str] = {}
            HTTPPropagator.inject(span.context, tracing_headers)
            for header_key, header_value in tracing_headers.items():
                self.headers.append((header_key, header_value.encode("utf-8")))

        # Dispatch generic event for DSM and other listeners
        core.dispatch(
            MessagingEvents.PRODUCE_START.value,
            (self.topic, self.value, self.key, self.headers, ctx, self.partition),
        )


@dataclass
class MessagingProduceFinishedEvent(FinishEvent):
    result: Any = None

    def __init__(
        self,
        ctx: core.ExecutionContext,
        error: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        result: Any,
    ):
        self.ctx = ctx
        self.error = error
        self.result = result
        self.event_name = MessagingEvents.PRODUCE_COMPLETED.value

        super().__post_init__()

    # def on_finish(self) -> None:
    #     pass
    # core.dispatch(self.event_name, (self.ctx, self.error, self.result))


@dataclass
class MessagingConsumeEvent(ContextEvent):
    def __init__(
        self,
        topic: Optional[str],
        bootstrap_servers: str,
        group_id: Optional[str],
        integration_config: Any,
        message: Any = None,
        distributed_context: Any = None,
        start_ns: Optional[int] = None,
    ):
        self.event_name = MessagingEvents.CONSUME.value
        self.span_type = SpanTypes.WORKER
        self.span_name = schematize_messaging_operation(CONSUME, provider="kafka", direction=SpanDirection.INBOUND)
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.integration_config = integration_config
        self.message = message
        self.distributed_context = distributed_context
        self.start_ns = start_ns
        self.tags = {
            COMPONENT: integration_config.integration_name,
            TOPIC: topic,
            MESSAGING_DESTINATION_NAME: topic,
            MESSAGING_SYSTEM: SERVICE,
            HOST_LIST: bootstrap_servers,
            SPAN_KIND: SpanKind.CONSUMER,
            GROUP_ID: group_id,
        }
        self.service = trace_utils.ext_service(None, integration_config)

        super().__post_init__()

    def on_context_started(self, ctx: core.ExecutionContext) -> None:
        span = ctx.span
        span._metrics[_SPAN_MEASURED_KEY] = 1

    def on_context_ended(
        self,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        core.dispatch(
            MessagingEvents.CONSUME_COMPLETED.value,
            (ctx, self.message, exc_info),
        )


@dataclass
class MessagingConsumeBatchEvent(ContextEvent):
    def __init__(
        self,
        bootstrap_servers: str,
        group_id: Optional[str],
        integration_config: Any,
    ):
        self.event_name = MessagingEvents.CONSUME_BATCH.value
        self.span_type = SpanTypes.WORKER
        self.span_name = schematize_messaging_operation(CONSUME, provider="kafka", direction=SpanDirection.INBOUND)
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.integration_config = integration_config
        self.tags = {
            COMPONENT: integration_config.integration_name,
            MESSAGING_SYSTEM: SERVICE,
            HOST_LIST: bootstrap_servers,
            SPAN_KIND: SpanKind.CONSUMER,
            GROUP_ID: group_id,
        }
        self.service = trace_utils.ext_service(None, integration_config)

        super().__post_init__()

    def on_context_started(self, ctx: core.ExecutionContext) -> None:
        span = ctx.span
        span._metrics[_SPAN_MEASURED_KEY] = 1

    def on_context_ended(
        self,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        messages = ctx.get_item("messages")

        core.dispatch(
            MessagingEvents.CONSUME_BATCH_COMPLETED.value,
            (ctx, messages, exc_info),
        )


@dataclass
class MessagingCommitEvent(BaseEvent):
    group_id: Optional[str] = None
    offsets: Any = None

    def __init__(self, group_id: Optional[str], offsets: Any):
        self.event_name = MessagingEvents.COMMIT.value
        self.group_id = group_id
        self.offsets = offsets

        super().__post_init__()

    def on_event(self, event) -> None:
        pass
