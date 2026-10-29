from ddtrace import config
from ddtrace.contrib.internal.kombu.utils import HEADER_POS
from ddtrace.contrib.internal.kombu.utils import PUBLISH_BODY_IDX
from ddtrace.contrib.internal.kombu.utils import get_exchange_from_args
from ddtrace.contrib.internal.kombu.utils import get_routing_key_from_args
from ddtrace.internal import core
from ddtrace.internal.datastreams.pipelines import get_pipelines_reporter
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def handle_kombu_produce(args, kwargs, span):
    from . import data_streams_processor as processor

    routing_key = get_routing_key_from_args(args)
    exchange = get_exchange_from_args(args)
    payload_size = 0
    payload_size += _calculate_byte_size(args[HEADER_POS])
    payload_size += _calculate_byte_size(args[PUBLISH_BODY_IDX])

    has_routing_key = str(bool(routing_key)).lower()

    # On the default (unnamed) exchange, the routing key is the destination queue name.
    if not exchange and routing_key:
        pathway_tags = ["direction:out", f"topic:{routing_key}", "type:rabbitmq"]
    else:
        pathway_tags = []
        for prefix, value in [
            ("direction", "out"),
            ("exchange", exchange),
            ("has_routing_key", has_routing_key),
            ("type", "rabbitmq"),
        ]:
            if value is not None:
                pathway_tags.append(f"{prefix}:{value}")

    if (p := processor()) is not None:
        ctx = p.set_checkpoint(pathway_tags, payload_size=payload_size, span=span)
        DsmPathwayCodec.encode(ctx, args[HEADER_POS])


def handle_kombu_consume(instance, message, span):
    from . import data_streams_processor as processor

    payload_size = 0
    payload_size += _calculate_byte_size(message.body)
    payload_size += _calculate_byte_size(message.headers)

    if (p := processor()) is not None:
        ctx = DsmPathwayCodec.decode(message.headers, p)
        queue = instance.queues[0].name if len(instance.queues) > 0 else ""
        ctx.set_checkpoint(["direction:in", f"topic:{queue}", "type:rabbitmq"], payload_size=payload_size, span=span)


def handle_kombu_produce_pipelines(args, kwargs, span):
    """Record a rabbitmq produce side for automatic DSM pipelines (DSM2-531)."""
    reporter = get_pipelines_reporter()
    if reporter is None:
        return
    try:
        exchange = get_exchange_from_args(args)
        routing_key = get_routing_key_from_args(args)
        # Demo topologies use the default exchange, where the routing key is the
        # destination queue name, so produce and consume sides match on
        # (queue_type, topic). Named-exchange fanouts that route the same publish
        # through multiple queue names will not match -- accepted demo limitation.
        topic = exchange or routing_key
        if topic:
            reporter.on_produce("rabbitmq", "", topic)
    except Exception:  # noqa: BLE001 - never raise into application code
        log.debug("pipelines: failed to record kombu produce side", exc_info=True)


def handle_kombu_consume_pipelines(instance, message, span):
    """Record a rabbitmq consume side and make it the current in-process context.

    The event fires when Consumer.receive returns, before the caller processes
    the message, so the context must stay current until the next consume on the
    thread replaces it (see set_current_consume).
    """
    reporter = get_pipelines_reporter()
    if reporter is None:
        return
    try:
        queue = instance.queues[0].name if len(instance.queues) > 0 else ""
        if queue:
            reporter.set_current_consume("rabbitmq", "", queue, queue)
    except Exception:  # noqa: BLE001 - never raise into application code
        log.debug("pipelines: failed to record kombu consume side", exc_info=True)


if config._data_streams_enabled:
    core.on("kombu.amqp.publish.pre", handle_kombu_produce)
    core.on("kombu.amqp.receive.post", handle_kombu_consume)

# Automatic pipelines hooks subscribe unconditionally: they are a no-op unless
# get_pipelines_reporter() returns a reporter (DD_DATA_STREAMS_PIPELINES_ENABLED
# and APM tracing on).
core.on("kombu.amqp.publish.pre", handle_kombu_produce_pipelines)
core.on("kombu.amqp.receive.post", handle_kombu_consume_pipelines)
