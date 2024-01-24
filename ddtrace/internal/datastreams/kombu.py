from ddtrace import config
from ddtrace.contrib.kombu.utils import HEADER_POS
from ddtrace.contrib.kombu.utils import PUBLISH_BODY_IDX
from ddtrace.contrib.kombu.utils import get_exchange_from_args
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def handle_kombu_produce(args, kwargs, span):
    from . import data_streams_processor as processor

    dsm_identifier = get_exchange_from_args(args)
    payload_size = 0
    payload_size += _calculate_byte_size(args[HEADER_POS])
    payload_size += _calculate_byte_size(args[PUBLISH_BODY_IDX])

    pathway = processor().set_checkpoint(
        ["direction:out", "exchange:" + dsm_identifier, "type:rabbitmq"], payload_size=payload_size, span=span
    )
    encoded_pathway = pathway.encode()
    args[HEADER_POS][PROPAGATION_KEY] = encoded_pathway


def handle_kombu_consume(message, span):
    from . import data_streams_processor as processor

    payload_size = 0
    payload_size += _calculate_byte_size(message.body)
    payload_size += _calculate_byte_size(message.headers)

    ctx = processor().decode_pathway(message.headers.get(PROPAGATION_KEY, None))
    exchange = message.delivery_info["exchange"]
    ctx.set_checkpoint(["direction:in", f"exchange:{exchange}", "type:rabbitmq"], payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("kombu.amqp.publish.pre", handle_kombu_produce)
    core.on("kombu.amqp.receive.post", handle_kombu_consume)
