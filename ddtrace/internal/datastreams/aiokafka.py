import time
from types import TracebackType
from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer
    from aiokafka.consumer.group_coordinator import GroupCoordinator
    from aiokafka.structs import ConsumerRecord
    from aiokafka.structs import TopicPartition


log = get_logger(__name__)


def dsm_aiokafka_send_start(
    topic: str,
    value: Optional[bytes],
    key: Optional[bytes],
    headers: List[Tuple[str, bytes]],
    span_ctx: core.ExecutionContext,
    _partition: Any,
) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor:
        return

    span = span_ctx.span

    payload_size = 0
    payload_size += _calculate_byte_size(value)
    payload_size += _calculate_byte_size(key)
    try:
        header_dict = {k: v for k, v in headers}
    except Exception:
        log.debug("Error converting headers for payload size calculation", exc_info=True)
        header_dict = {}
    payload_size += _calculate_byte_size(header_dict)

    edge_tags = ["direction:out", f"topic:{topic}", "type:kafka"]
    ctx = dsm_processor.set_checkpoint(edge_tags, payload_size=payload_size, span=span)

    dsm_headers: Dict[str, str] = {}
    DsmPathwayCodec.encode(ctx, dsm_headers)
    for header_key, header_value in dsm_headers.items():
        headers.append((header_key, header_value.encode("utf-8")))


def dsm_aiokafka_send_completed(
    _ctx: core.ExecutionContext,
    _error: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    record_metadata: Any,
) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor:
        return

    if record_metadata is not None:
        reported_offset = record_metadata.offset if isinstance(record_metadata.offset, int) else -1
        dsm_processor.track_kafka_produce(
            record_metadata.topic, record_metadata.partition, reported_offset, time.time()
        )


def dsm_aiokafka_message_consume(
    instance: "AIOKafkaConsumer",
    span_ctx: core.ExecutionContext,
    _start_ns: Optional[int],
    message: Optional["ConsumerRecord"],
    _error: Optional[BaseException],
) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor or not message:
        return

    span = span_ctx.span

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

    ctx = DsmPathwayCodec.decode(headers, dsm_processor)
    ctx.set_checkpoint(
        ["direction:in", f"group:{group}", f"topic:{message.topic}", "type:kafka"], payload_size=payload_size, span=span
    )

    if instance._enable_auto_commit:
        # it's not exactly true, but if auto commit is enabled, we consider that a message is acknowledged
        # when it's read. We add one because the commit offset is the next message to read.
        reported_offset = (message.offset + 1) if isinstance(message.offset, int) else -1
        dsm_processor.track_kafka_commit(
            instance._group_id, message.topic, message.partition, reported_offset, time.time()
        )


def dsm_aiokafka_many_messages_consume(
    instance: "AIOKafkaConsumer",
    ctx: core.ExecutionContext,
    messages: Optional[Dict["TopicPartition", list["ConsumerRecord"]]],
) -> None:
    if messages is not None:
        for _, records in messages.items():
            for record in records:
                dsm_aiokafka_message_consume(instance, ctx, None, record, None)


def dsm_aiokafka_message_commit(instance: "GroupCoordinator", args: Any, kwargs: Any) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor:
        return

    offsets = get_argument_value(args, kwargs, 1, "offsets", optional=True)
    if offsets:
        for tp, offset in offsets.items():
            # offset can be either an int or a tuple of (offset, metadata)
            if isinstance(offset, int):
                reported_offset = offset
            elif isinstance(offset, tuple) and len(offset) > 0 and isinstance(offset[0], int):
                reported_offset = offset[0]
            else:
                reported_offset = -1
            dsm_processor.track_kafka_commit(instance.group_id, tp.topic, tp.partition, reported_offset, time.time())


if config._data_streams_enabled:
    core.on("aiokafka.send.start", dsm_aiokafka_send_start)
    core.on("aiokafka.send.completed", dsm_aiokafka_send_completed)
    core.on("aiokafka.getone.message", dsm_aiokafka_message_consume)
    core.on("aiokafka.getmany.message", dsm_aiokafka_many_messages_consume)
    core.on("aiokafka.commit.end", dsm_aiokafka_message_commit)
