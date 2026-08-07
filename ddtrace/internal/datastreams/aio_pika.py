from ddtrace.contrib._events.messaging import AioPikaEvents
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size

from . import data_streams_processor as get_processor


# AIDEV-NOTE: The aio-pika patch imports this module only when DSM is enabled.
# Keeping registration integration-driven avoids the circular dependency that
# results when ddtrace.internal.datastreams imports this plugin during startup.


def handle_aio_pika_produce(ctx: core.ExecutionContext) -> None:  # type: ignore[type-arg]
    proc = get_processor()  # type: ignore[no-untyped-call]
    if proc is None:
        return

    event = ctx.event
    headers = event.headers
    exchange_name = event.destination
    has_routing_key = str(bool(event.routing_key)).lower()
    payload_size = _calculate_byte_size(event.body) + _calculate_byte_size(headers)  # type: ignore[no-untyped-call]

    pathway_tags = [
        "direction:out",
        "exchange:" + (exchange_name or ""),
        "has_routing_key:" + has_routing_key,
        "type:rabbitmq",
    ]

    # span is optional — DSM works when APM tracing is disabled
    dsm_ctx = proc.set_checkpoint(pathway_tags, payload_size=payload_size, span=ctx._inner_span)
    # For publish, event.headers IS the message's header dict, so encoding
    # into it propagates DSM context onto the wire automatically.
    DsmPathwayCodec.encode(dsm_ctx, headers)


def handle_aio_pika_consume(ctx: core.ExecutionContext) -> None:  # type: ignore[type-arg]
    proc = get_processor()  # type: ignore[no-untyped-call]
    if proc is None:
        return

    event = ctx.event
    headers = event.headers
    queue_name = event.destination
    payload_size = _calculate_byte_size(event.body) + _calculate_byte_size(headers)  # type: ignore[no-untyped-call]

    dsm_ctx = DsmPathwayCodec.decode(headers, proc)
    # span is optional — DSM works when APM tracing is disabled
    dsm_ctx.set_checkpoint(  # type: ignore[no-untyped-call]
        ["direction:in", "topic:" + (queue_name or ""), "type:rabbitmq"],
        payload_size=payload_size,
        span=ctx._inner_span,
    )


core.on(f"context.started.{AioPikaEvents.PUBLISH.value}", handle_aio_pika_produce)
core.on(f"context.started.{AioPikaEvents.CONSUME.value}", handle_aio_pika_consume)
