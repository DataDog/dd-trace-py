from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size


def handle_aio_pika_produce(ctx: core.ExecutionContext) -> None:
    # AIDEV-NOTE: inline import avoids a circular import — this module is loaded
    # at import time via the `if config._data_streams_enabled` block below, which
    # can happen before the data_streams_processor is fully initialised.
    from . import data_streams_processor as get_processor

    proc = get_processor()
    if proc is None:
        return

    event = ctx.event
    headers = event.headers
    exchange_name = event.destination
    payload_size = _calculate_byte_size(event.body) + _calculate_byte_size(headers)

    pathway_tags = [
        "direction:out",
        "exchange:" + (exchange_name or ""),
        "has_routing_key:false",
        "type:rabbitmq",
    ]

    # span is optional — DSM works when APM tracing is disabled
    dsm_ctx = proc.set_checkpoint(pathway_tags, payload_size=payload_size, span=ctx._inner_span)
    # For publish, event.headers IS the message's header dict, so encoding
    # into it propagates DSM context onto the wire automatically.
    DsmPathwayCodec.encode(dsm_ctx, headers)


def handle_aio_pika_consume(ctx: core.ExecutionContext) -> None:
    from . import data_streams_processor as get_processor

    proc = get_processor()
    if proc is None:
        return

    event = ctx.event
    headers = event.headers
    queue_name = event.destination
    payload_size = _calculate_byte_size(event.body) + _calculate_byte_size(headers)

    dsm_ctx = DsmPathwayCodec.decode(headers, proc)
    # span is optional — DSM works when APM tracing is disabled
    dsm_ctx.set_checkpoint(
        ["direction:in", "topic:" + (queue_name or ""), "type:rabbitmq"],
        payload_size=payload_size,
        span=ctx._inner_span,
    )


if config._data_streams_enabled:
    core.on("context.started.messaging.publish.aio-pika", handle_aio_pika_produce)
    core.on("context.started.messaging.consume.aio-pika", handle_aio_pika_consume)
