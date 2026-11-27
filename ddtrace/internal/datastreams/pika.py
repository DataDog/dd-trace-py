from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size


def _produce_edge_tags(exchange: str, routing_key: str) -> list[str]:
    if not exchange and routing_key:
        return ["direction:out", f"topic:{routing_key}", "type:rabbitmq"]
    return [
        "direction:out",
        f"exchange:{exchange}",
        f"has_routing_key:{str(bool(routing_key)).lower()}",
        "type:rabbitmq",
    ]


def handle_pika_publish(
    exchange: str, routing_key: str, body: Any, headers: dict[str, Any], span: Optional[Any]
) -> None:
    from . import data_streams_processor as processor

    if (p := processor()) is None:
        return
    payload_size = _calculate_byte_size(body) + _calculate_byte_size(headers)
    ctx = p.set_checkpoint(_produce_edge_tags(exchange, routing_key), payload_size=payload_size, span=span)
    DsmPathwayCodec.encode(ctx, headers)


def handle_pika_receive(queue: str, headers: dict[str, Any], body: Any, span: Optional[Any]) -> None:
    from . import data_streams_processor as processor

    if (p := processor()) is None:
        return
    payload_size = _calculate_byte_size(body) + _calculate_byte_size(headers)
    ctx = DsmPathwayCodec.decode(headers, p)
    ctx.set_checkpoint(["direction:in", f"topic:{queue}", "type:rabbitmq"], payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("pika.basic_publish.pre", handle_pika_publish)
    core.on("pika.receive.pre", handle_pika_receive)
