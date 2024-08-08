import time

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.utils import get_argument_value


INT_TYPES = (int,)


def dsm_kafka_message_produce(instance, topic, value, key, headers, span):
    # TODO this doesn't capture latency timings yet
    from . import data_streams_processor as processor

    payload_size = 0
    payload_size += _calculate_byte_size(value)
    payload_size += _calculate_byte_size(key)
    payload_size += _calculate_byte_size(headers)

    ctx = processor().set_checkpoint(
        ["direction:out", "topic:" + topic, "type:kafka"], payload_size=payload_size, span=span
    )
    dsm_headers = {}
    DsmPathwayCodec.encode(ctx, dsm_headers)
    for key, value in dsm_headers.items():
        headers.append((key, value.encode("utf-8")))


def dsm_kafka_message_consume(instance, message, span):
    from . import data_streams_processor as processor

    headers = {header[0]: header[1].decode("utf-8") for header in (message.headers or [])}
    topic = core.get_item("kafka_topic")
    group = instance._group_id

    payload_size = 0
    payload_size += _calculate_byte_size(message.value)
    payload_size += _calculate_byte_size(message.key)
    payload_size += _calculate_byte_size(headers)

    ctx = DsmPathwayCodec.decode(headers, processor())
    ctx.set_checkpoint(
        ["direction:in", "group:" + group, "topic:" + topic, "type:kafka"], payload_size=payload_size, span=span
    )

    if instance._enable_auto_commit:
        # it's not exactly true, but if auto commit is enabled, we consider that a message is acknowledged
        # when it's read. We add one because the commit offset is the next message to read.
        reported_offset = (message.offset + 1) if isinstance(message.offset, INT_TYPES) else -1
        processor().track_kafka_commit(
            instance._group_id, message.topic, message.partition, reported_offset, time.time()
        )


def dsm_kafka_message_commit(instance, args, kwargs):
    from . import data_streams_processor as processor

    offsets = get_argument_value(args, kwargs, 0, "offsets", optional=True)
    for tp, offset in offsets.items():
        reported_offset = offset if isinstance(offset, INT_TYPES) else -1
        processor().track_kafka_commit(instance._group_id, tp.topic, tp.partition, reported_offset, time.time())


if config._data_streams_enabled:
    core.on("aiokafka.produce.start", dsm_kafka_message_produce)
    core.on("aiokafka.consume.start", dsm_kafka_message_consume)
    core.on("aiokafka.commit.start", dsm_kafka_message_commit)
