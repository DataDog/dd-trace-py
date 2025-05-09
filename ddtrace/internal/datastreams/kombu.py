from ddtrace import config
from ddtrace.contrib.internal.kombu.utils import HEADER_POS
from ddtrace.contrib.internal.kombu.utils import PUBLISH_BODY_IDX
from ddtrace.contrib.internal.kombu.utils import get_exchange_from_args
from ddtrace.contrib.internal.kombu.utils import get_routing_key_from_args
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def handle_kombu_produce(args, kwargs, span):
    from . import data_streams_processor as processor

    routing_key = get_routing_key_from_args(args)
    dsm_identifier = get_exchange_from_args(args)
    payload_size = 0
    payload_size += _calculate_byte_size(args[HEADER_POS])
    payload_size += _calculate_byte_size(args[PUBLISH_BODY_IDX])

    has_routing_key = str(bool(routing_key)).lower()

    pathway_tags = []
    for prefix, value in [
        ("direction", "out"),
        ("exchange", dsm_identifier),
        ("has_routing_key", has_routing_key),
        ("type", "rabbitmq"),
    ]:
        if value is not None:
            pathway_tags.append(f"{prefix}:{value}")

    ctx = processor().set_checkpoint(pathway_tags, payload_size=payload_size, span=span)
    DsmPathwayCodec.encode(ctx, args[HEADER_POS])


def handle_kombu_consume(instance, message, span):
    from . import data_streams_processor as processor

    payload_size = 0
    payload_size += _calculate_byte_size(message.body)
    payload_size += _calculate_byte_size(message.headers)

    ctx = DsmPathwayCodec.decode(message.headers, processor())
    queue = instance.queues[0].name if len(instance.queues) > 0 else ""
    ctx.set_checkpoint(["direction:in", f"topic:{queue}", "type:rabbitmq"], payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("kombu.amqp.publish.pre", handle_kombu_produce)
    core.on("kombu.amqp.receive.post", handle_kombu_consume)
