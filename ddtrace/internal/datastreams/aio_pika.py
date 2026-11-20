from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size


def _processor() -> Optional[DataStreamsProcessor]:
    # Checked on every call, not at import, so the handlers stay inert while DSM is disabled.
    if not config._data_streams_enabled:
        return None
    from . import data_streams_processor

    return data_streams_processor()


def _payload_size(body: Any, headers: Optional[dict[str, Any]]) -> int:
    return _calculate_byte_size(body or b"") + _calculate_byte_size(headers or {})


def handle_aio_pika_publish(
    exchange: str, routing_key: str, headers: Optional[dict[str, Any]], body: Any, span: Any
) -> None:
    processor = _processor()
    if processor is None:
        return

    # Same pathway tags as the kombu integration, so producers on either AMQP client link to the same consumers.
    # On the default (unnamed) exchange, the routing key is the destination queue name.
    if not exchange and routing_key:
        pathway_tags = ["direction:out", f"topic:{routing_key}", "type:rabbitmq"]
    else:
        pathway_tags = [
            "direction:out",
            f"exchange:{exchange}",
            f"has_routing_key:{str(bool(routing_key)).lower()}",
            "type:rabbitmq",
        ]

    ctx = processor.set_checkpoint(pathway_tags, payload_size=_payload_size(body, headers), span=span)
    if headers is not None:
        DsmPathwayCodec.encode(ctx, headers)


def handle_aio_pika_receive(queue: str, message: Any, span: Any) -> None:
    processor = _processor()
    if processor is None:
        return

    headers = getattr(message, "headers", None) or {}
    ctx = DsmPathwayCodec.decode(headers, processor)
    ctx.set_checkpoint(
        ["direction:in", f"topic:{queue}", "type:rabbitmq"],
        payload_size=_payload_size(getattr(message, "body", None), headers),
        span=span,
    )


core.on("aio_pika.publish.start", handle_aio_pika_publish)
core.on("aio_pika.receive.post", handle_aio_pika_receive)
