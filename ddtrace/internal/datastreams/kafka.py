from dataclasses import dataclass
from enum import Enum
import time
from typing import Any

from confluent_kafka import TopicPartition

from ddtrace._trace.span import Span
from ddtrace.internal.core.events import BaseEvent
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value


INT_TYPES = (int,)
MESSAGE_ARG_POSITION = 1
KEY_ARG_POSITION = 2
KEY_KWARG_NAME = "key"

disable_header_injection = False

log = get_logger(__name__)


class KafkaDsmEvents(Enum):
    KAFKA_PRODUCE_START = "kafka.produce.start"
    KAFKA_CONSUME_START = "kafka.consume.start"
    KAFKA_COMMIT_START = "kafka.commit.start"


@dataclass
class KafkaDsmProduceEvent(BaseEvent):
    event_name = KafkaDsmEvents.KAFKA_PRODUCE_START.value

    args: Any
    kwargs: Any
    key: Any
    message: Any
    headers: Any
    is_serializing: Any
    topic: Any
    cluster_id: Any
    span: Any

    @classmethod
    def on_event(cls, event_instance, *additional_args):
        from . import data_streams_processor as processor

        payload_size = 0
        payload_size += _calculate_byte_size(event_instance.message)
        payload_size += _calculate_byte_size(event_instance.key)
        payload_size += _calculate_byte_size(event_instance.headers)

        edge_tags = ["direction:out", "topic:" + event_instance.topic, "type:kafka"]
        if event_instance.cluster_id:
            edge_tags.append("kafka_cluster_id:" + str(event_instance.cluster_id))

        ctx = processor().set_checkpoint(edge_tags, payload_size=payload_size, span=event_instance.span)
        if not disable_header_injection:
            DsmPathwayCodec.encode(ctx, event_instance.headers)
            event_instance.kwargs["headers"] = event_instance.headers

        on_delivery_kwarg = "on_delivery"
        on_delivery_arg = 5
        on_delivery = None
        try:
            on_delivery = get_argument_value(
                event_instance.args, event_instance.kwargs, on_delivery_arg, on_delivery_kwarg
            )
        except ArgumentError:
            if not event_instance.is_serializing:
                on_delivery_kwarg = "callback"
                on_delivery_arg = 4
                on_delivery = get_argument_value(
                    event_instance.args, event_instance.kwargs, on_delivery_arg, on_delivery_kwarg, optional=True
                )

        def wrapped_callback(err, msg):
            global disable_header_injection
            if err is None:
                reported_offset = msg.offset() if isinstance(msg.offset(), INT_TYPES) else -1
                processor().track_kafka_produce(msg.topic(), msg.partition(), reported_offset, time.time())
            elif err.code() == -1 and not disable_header_injection:
                disable_header_injection = True
                log.error(
                    "Kafka Broker responded with UNKNOWN_SERVER_ERROR (-1). Please look at broker logs for more "
                    "information. Tracer message header injection for Kafka is disabled."
                )
            if on_delivery is not None:
                on_delivery(err, msg)

        try:
            event_instance.args, event_instance.kwargs = set_argument_value(
                event_instance.args, event_instance.kwargs, on_delivery_arg, on_delivery_kwarg, wrapped_callback
            )
        except ArgumentError:
            # we set the callback even if it's not set by the client, to track produce calls correctly.
            event_instance.kwargs[on_delivery_kwarg] = wrapped_callback


@dataclass
class KafkaDsmConsumeEvent(BaseEvent):
    event_name = KafkaDsmEvents.KAFKA_CONSUME_START.value

    message: Any
    instance: Any
    kafka_topic: Any
    cluster_id: Any
    span: Span

    @classmethod
    def on_event(cls, event_instance, *additional_args):
        from . import data_streams_processor as processor

        message = event_instance.message
        instance = event_instance.instance
        span = event_instance.span
        topic = event_instance.kafka_topic
        cluster_id = event_instance.cluster_id
        headers = {header[0]: header[1] for header in (message.headers() or [])}
        group = instance._group_id

        payload_size = 0
        if hasattr(message, "len"):
            # message.len() is only supported for some versions of confluent_kafka
            payload_size += message.len()
        else:
            payload_size += _calculate_byte_size(message.value())

        payload_size += _calculate_byte_size(message.key())
        payload_size += _calculate_byte_size(headers)

        ctx = DsmPathwayCodec.decode(headers, processor())

        edge_tags = ["direction:in", "group:" + group, "topic:" + topic, "type:kafka"]
        if cluster_id:
            edge_tags.append("kafka_cluster_id:" + str(cluster_id))

        ctx.set_checkpoint(
            edge_tags,
            payload_size=payload_size,
            span=span,
        )

        if instance._auto_commit:
            # it's not exactly true, but if auto commit is enabled, we consider that a message is acknowledged
            # when it's read. We add one because the commit offset is the next message to read.
            reported_offset = (message.offset() + 1) if isinstance(message.offset(), INT_TYPES) else -1
            processor().track_kafka_commit(
                instance._group_id, message.topic(), message.partition(), reported_offset, time.time()
            )


@dataclass
class KafkaDsmCommitEvent(BaseEvent):
    event_name = KafkaDsmEvents.KAFKA_COMMIT_START.value

    instance: Any
    message: Any
    offsets: Any

    @classmethod
    def on_event(cls, event_instance, *additional_args):
        from . import data_streams_processor as processor

        message = event_instance.message
        instance = event_instance.instance

        offsets = []
        if message is not None:
            # the commit offset is the next message to read. So last message read + 1
            reported_offset = message.offset() + 1 if isinstance(message.offset(), INT_TYPES) else -1
            offsets = [TopicPartition(message.topic(), message.partition(), reported_offset)]
        else:
            offsets = event_instance.offsets

        for offset in offsets:
            reported_offset = offset.offset if isinstance(offset.offset, INT_TYPES) else -1
            processor().track_kafka_commit(
                instance._group_id, offset.topic, offset.partition, reported_offset, time.time()
            )
