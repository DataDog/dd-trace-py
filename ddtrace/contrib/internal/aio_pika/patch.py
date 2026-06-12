"""
Instrumentation for aio-pika (asyncio AMQP client for RabbitMQ).

Two critical instrumentation targets:
  - Exchange.publish  — producer span, injects trace context into message headers
  - consumer()        — module-level function in aio_pika.queue, consumer span,
                        extracts trace context from incoming message headers
"""

import sys
from typing import Any
from typing import Callable
from typing import Optional

import aio_pika
import aio_pika.exchange
import aio_pika.queue
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)

_MESSAGING_SYSTEM = "rabbitmq"

config._add(  # type: ignore[no-untyped-call]
    "aio_pika",
    dict(
        _default_service=schematize_service_name("aio_pika"),  # type: ignore[operator]
        distributed_tracing_enabled=asbool(env.get("DD_AIO_PIKA_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version() -> str:
    return getattr(aio_pika, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aio_pika": ">=9.0.0"}


def patch() -> None:
    if getattr(aio_pika, "_datadog_patch", False):
        return
    aio_pika._datadog_patch = True

    _w("aio_pika.exchange", "Exchange.publish", _traced_publish)
    _w("aio_pika.queue", "consumer", _traced_consumer)


def unpatch() -> None:
    if not getattr(aio_pika, "_datadog_patch", False):
        return
    aio_pika._datadog_patch = False

    _u(aio_pika.exchange.Exchange, "publish")
    _u(aio_pika.queue, "consumer")


async def _traced_publish(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Wrap Exchange.publish to create a producer span and inject trace context."""
    # args: (message, routing_key, *, mandatory, immediate, timeout)
    message = args[0] if args else kwargs.get("message")
    routing_key: str = args[1] if len(args) > 1 else kwargs.get("routing_key", "")

    exchange_name: str = getattr(instance, "name", "") or ""
    destination = exchange_name or routing_key or ""

    tags: dict[str, Any] = {
        COMPONENT: config.aio_pika.integration_name,
        SPAN_KIND: SpanKind.PRODUCER,
        MESSAGING_SYSTEM: _MESSAGING_SYSTEM,
    }
    if destination:
        tags[MESSAGING_DESTINATION_NAME] = destination

    with core.context_with_data(  # type: ignore[no-untyped-call]
        "aio_pika.publish",
        span_name=schematize_messaging_operation("amqp.send", provider="rabbitmq", direction=SpanDirection.OUTBOUND),  # type: ignore[operator]
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.aio_pika),
        tags=tags,
        integration_config=config.aio_pika,
    ) as ctx:
        if config.aio_pika.distributed_tracing_enabled and message is not None:
            # Inject trace context into message headers
            properties = getattr(message, "properties", None)
            if properties is not None:
                if properties.headers is None:
                    properties.headers = {}
                if ctx.span is not None:
                    HTTPPropagator.inject(ctx.span.context, properties.headers)

        try:
            result = await wrapped(*args, **kwargs)
            return result
        except BaseException:
            if ctx.span is not None:
                ctx.span.set_exc_info(*sys.exc_info())
            raise


async def _traced_consumer(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Wrap the module-level consumer() function to create a consumer span and extract trace context.

    Signature: async def consumer(callback, msg, *, no_ack) -> Any
    """
    # args: (callback, msg)
    msg = args[1] if len(args) > 1 else kwargs.get("msg")

    # Extract routing/queue name from the delivered message
    destination: str = ""
    if msg is not None:
        routing_key: Optional[str] = getattr(msg, "routing_key", None)
        if routing_key:
            destination = routing_key

    parent_ctx = None
    if config.aio_pika.distributed_tracing_enabled and msg is not None:
        header = getattr(msg, "header", None)
        properties = getattr(header, "properties", None) if header is not None else None
        if properties is not None:
            headers = getattr(properties, "headers", None)
            if headers:
                # Headers may contain bytes values — normalize to str for propagator
                str_headers: dict[str, str] = {}
                for k, v in headers.items():
                    if isinstance(v, (bytes, bytearray)):
                        str_headers[str(k)] = v.decode("utf-8", errors="ignore")
                    else:
                        str_headers[str(k)] = str(v)
                parent_ctx = HTTPPropagator.extract(str_headers)  # type: ignore[no-untyped-call]

    tags: dict[str, Any] = {
        COMPONENT: config.aio_pika.integration_name,
        SPAN_KIND: SpanKind.CONSUMER,
        MESSAGING_SYSTEM: _MESSAGING_SYSTEM,
    }
    if destination:
        tags[MESSAGING_DESTINATION_NAME] = destination

    with core.context_with_data(  # type: ignore[no-untyped-call]
        "aio_pika.consume",
        call_trace=False,
        span_name=schematize_messaging_operation("amqp.receive", provider="rabbitmq", direction=SpanDirection.INBOUND),  # type: ignore[operator]
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.aio_pika),
        distributed_context=parent_ctx,
        tags=tags,
        integration_config=config.aio_pika,
    ) as ctx:
        try:
            return await wrapped(*args, **kwargs)
        except BaseException:
            if ctx.span is not None:
                ctx.span.set_exc_info(*sys.exc_info())
            raise
