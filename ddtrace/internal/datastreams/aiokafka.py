import time

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.utils import get_argument_value


INT_TYPES = (int,)


def dsm_aiokafka_send_start(topic, value, key, headers, span, _):
    from . import data_streams_processor as processor

    payload_size = 0
    payload_size += _calculate_byte_size(value)
    payload_size += _calculate_byte_size(key)
    try:
        header_dict = {
            k: (
                v.decode("utf-8", errors="ignore") if isinstance(v, (bytes, bytearray)) else "" if v is None else str(v)
            )
            for k, v in headers
        }
    except Exception:
        header_dict = {}
    payload_size += _calculate_byte_size(header_dict)

    edge_tags = ["direction:out", "topic:" + topic, "type:kafka"]
    ctx = processor().set_checkpoint(edge_tags, payload_size=payload_size, span=span)

    dsm_headers = {}
    DsmPathwayCodec.encode(ctx, dsm_headers)
    for key, value in dsm_headers.items():
        headers.append((key, value.encode("utf-8")))


def dsm_aiokafka_send_completed(record_metadata):
    from . import data_streams_processor as processor

    reported_offset = record_metadata.offset if isinstance(record_metadata.offset, INT_TYPES) else -1
    processor().track_kafka_produce(record_metadata.topic, record_metadata.partition, reported_offset, time.time())


def dsm_aiokafka_message_consume(instance, span, message, _):
    from . import data_streams_processor as processor

    headers = {
        key: val.decode("utf-8", errors="ignore") if isinstance(val, (bytes, bytearray)) else str(val)
        for key, val in (message.headers or [])
        if val is not None
    }
    group = instance._group_id

    payload_size = 0
    payload_size += _calculate_byte_size(message.value)
    payload_size += _calculate_byte_size(message.key)
    payload_size += _calculate_byte_size(headers)

    ctx = DsmPathwayCodec.decode(headers, processor())
    ctx.set_checkpoint(
        ["direction:in", "group:" + group, "topic:" + message.topic, "type:kafka"], payload_size=payload_size, span=span
    )

    if instance._enable_auto_commit:
        # it's not exactly true, but if auto commit is enabled, we consider that a message is acknowledged
        # when it's read. We add one because the commit offset is the next message to read.
        reported_offset = (message.offset + 1) if isinstance(message.offset, INT_TYPES) else -1
        processor().track_kafka_commit(
            instance._group_id, message.topic, message.partition, reported_offset, time.time()
        )


def dsm_aiokafka_many_messages_consume(instance, span, messages):
    if messages is not None:
        for _, records in messages.items():
            for record in records:
                dsm_aiokafka_message_consume(instance, span, record, None)


def dsm_aiokafka_messsage_commit(instance, args, kwargs):
    from . import data_streams_processor as processor

    offsets = get_argument_value(args, kwargs, 0, "offsets", optional=True)
    if offsets:
        for tp, offset in offsets.items():
            # offset can be either an int or a tuple of (offset, metadata)
            if isinstance(offset, INT_TYPES):
                reported_offset = offset
            elif isinstance(offset, tuple) and len(offset) > 0 and isinstance(offset[0], INT_TYPES):
                reported_offset = offset[0]
            else:
                reported_offset = -1
            processor().track_kafka_commit(instance._group_id, tp.topic, tp.partition, reported_offset, time.time())


if config._data_streams_enabled:
    core.on("aiokafka.send.start", dsm_aiokafka_send_start)
    core.on("aiokafka.send.completed", dsm_aiokafka_send_completed)
    core.on("aiokafka.getone.message", dsm_aiokafka_message_consume)
    core.on("aiokafka.getmany.message", dsm_aiokafka_many_messages_consume)
    core.on("aiokafka.commit.start", dsm_aiokafka_messsage_commit)
