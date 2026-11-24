from __future__ import annotations

import time
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context


log = get_logger(__name__)


def _headers_as_str(headers: Any) -> dict[str, str]:
    return {
        key: val.decode("utf-8", errors="ignore") if isinstance(val, (bytes, bytearray)) else str(val)
        for key, val in (headers or [])
        if val is not None
    }


def _headers_byte_size(headers: Any) -> int:
    return sum(_calculate_byte_size(key) + _calculate_byte_size(val) for key, val in (headers or []))


def dsm_kafka_python_send_start(
    topic: str,
    value: Any,
    key: Any,
    headers: list[tuple[str, bytes]],
    span_ctx: core.ExecutionContext[Any],
) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor:
        return

    payload_size = _calculate_byte_size(value) + _calculate_byte_size(key) + _headers_byte_size(headers)

    cluster_id = core.find_item("kafka_cluster_id")
    edge_tags = ["direction:out", f"topic:{topic}", "type:kafka"]
    if cluster_id:
        edge_tags.append(f"kafka_cluster_id:{cluster_id}")
    ctx = dsm_processor.set_checkpoint(edge_tags, payload_size=payload_size, span=span_from_context(span_ctx))

    dsm_headers: dict[str, str] = {}
    DsmPathwayCodec.encode(ctx, dsm_headers)
    headers.extend((header_key, header_value.encode("utf-8")) for header_key, header_value in dsm_headers.items())


def dsm_kafka_python_send_completed(record_metadata: Any, cluster_id: str) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor or record_metadata is None:
        return

    offset = record_metadata.offset if isinstance(record_metadata.offset, int) else -1
    dsm_processor.track_kafka_produce(
        record_metadata.topic,
        record_metadata.partition,
        offset,
        time.time(),
        cluster_id=cluster_id,
    )


def dsm_kafka_python_message_consume(instance: Any, span_ctx: core.ExecutionContext[Any], record: Any) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor or record is None:
        return

    headers = _headers_as_str(record.headers)
    group = instance.config.get("group_id")
    cluster_id = core.find_item("kafka_cluster_id")

    payload_size = (
        _calculate_byte_size(record.value) + _calculate_byte_size(record.key) + _headers_byte_size(record.headers)
    )

    ctx = DsmPathwayCodec.decode(headers, dsm_processor)
    edge_tags = ["direction:in", f"topic:{record.topic}", "type:kafka"]
    if group:
        edge_tags.append(f"group:{group}")
    if cluster_id:
        edge_tags.append(f"kafka_cluster_id:{cluster_id}")
    ctx.set_checkpoint(edge_tags, payload_size=payload_size, span=span_from_context(span_ctx))


def _committed_offset(offset: Any) -> int:
    if isinstance(offset, int):
        return offset
    value: Optional[Any] = getattr(offset, "offset", None)
    if value is None and isinstance(offset, tuple) and offset:
        value = offset[0]
    return value if isinstance(value, int) else -1


def dsm_kafka_python_commit(group_id: Optional[str], offsets: dict[Any, Any], cluster_id: str) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor or not group_id:
        return

    now = time.time()
    for tp, offset in offsets.items():
        dsm_processor.track_kafka_commit(
            group_id, tp.topic, tp.partition, _committed_offset(offset), now, cluster_id=cluster_id
        )


if config._data_streams_enabled:
    core.on("kafka_python.send.start", dsm_kafka_python_send_start)
    core.on("kafka_python.send.completed", dsm_kafka_python_send_completed)
    core.on("kafka_python.consume.message", dsm_kafka_python_message_consume)
    core.on("kafka_python.commit", dsm_kafka_python_commit)
