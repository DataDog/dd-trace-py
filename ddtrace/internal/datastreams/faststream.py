"""Data Streams Monitoring (DSM) handlers for the FastStream integration.

The FastStream contrib middleware emits two events:

- ``faststream.publish.pre`` — fired immediately before a message is handed to
  the underlying broker driver. Handler injects the DSM pathway header into
  ``cmd.headers`` (a dict).
- ``faststream.consume.post`` — fired right after a message arrives and a
  consumer span has been created. Handler decodes any inbound pathway header
  and chains a consumer-side checkpoint.

Both events pass the destination string already extracted by the per-driver
tag-extractors in ``ddtrace.contrib.internal.faststream._middleware`` so that
DSM does not need to re-walk ``raw_message`` (whose accessors differ between
aiokafka and confluent_kafka).
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from faststream.message import StreamMessage
    from faststream.response import PublishCommand


log = get_logger(__name__)


def _destination_tag_key(messaging_system: str) -> str:
    """Edge-tag key for the destination, matching ddtrace's per-broker convention.

    Datadog DSM uses ``topic:`` for Kafka and Rabbit (kombu sets ``topic:``
    even though it's an AMQP queue), ``subject:`` for NATS, ``channel:`` for
    Redis. Kept consistent with the existing aiokafka / kombu integrations.
    """
    return {
        "kafka": "topic",
        "rabbitmq": "topic",
        "nats": "subject",
        "redis": "channel",
    }.get(messaging_system, "topic")


def dsm_faststream_publish_pre(
    cmd: "PublishCommand",
    span: Any,
    messaging_system: str,
    destination: Optional[str] = None,
) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor:
        return

    if destination is None:
        destination = getattr(cmd, "destination", "") or ""
    body = getattr(cmd, "body", None)
    if body is None:
        body = getattr(cmd, "message", None)

    headers = getattr(cmd, "headers", None)
    if headers is None:
        cmd.headers = {}
        headers = cmd.headers

    payload_size = _calculate_byte_size(body) + _calculate_byte_size(headers)

    edge_tags = [
        "direction:out",
        f"{_destination_tag_key(messaging_system)}:{destination}",
        f"type:{messaging_system}",
    ]
    ctx = dsm_processor.set_checkpoint(edge_tags, payload_size=payload_size, span=span)
    DsmPathwayCodec.encode(ctx, headers)


def dsm_faststream_consume_post(
    msg: "StreamMessage[Any]",
    span: Any,
    messaging_system: str,
    group_id: Optional[str] = None,
    destination: Optional[str] = None,
) -> None:
    from . import data_streams_processor as processor

    dsm_processor = processor()
    if not dsm_processor:
        return

    headers = getattr(msg, "headers", None) or {}
    # FastStream normalizes headers to a dict[str, str|bytes] across drivers.
    decoded_headers = {
        k: (v.decode("utf-8", errors="ignore") if isinstance(v, (bytes, bytearray)) else str(v))
        for k, v in headers.items()
        if v is not None
    }

    body = getattr(msg, "body", None)
    payload_size = _calculate_byte_size(body) + _calculate_byte_size(decoded_headers)

    edge_tags = ["direction:in"]
    if group_id:
        edge_tags.append(f"group:{group_id}")
    edge_tags.append(f"{_destination_tag_key(messaging_system)}:{destination or ''}")
    edge_tags.append(f"type:{messaging_system}")

    ctx = DsmPathwayCodec.decode(decoded_headers, dsm_processor)
    ctx.set_checkpoint(edge_tags, payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("faststream.publish.pre", dsm_faststream_publish_pre)
    core.on("faststream.consume.post", dsm_faststream_consume_post)
