from time import time_ns
from typing import Any
from typing import Callable

import aio_pika
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.messaging import AioPikaEvents
from ddtrace.contrib._events.messaging import MessagingActionEvent
from ddtrace.contrib._events.messaging import MessagingConsumeEvent
from ddtrace.contrib._events.messaging import MessagingPublishEvent
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


log = get_logger(__name__)

_MESSAGING_SYSTEM = "rabbitmq"

config._add(  # type: ignore[no-untyped-call]
    "aio_pika",
    dict(
        distributed_tracing_enabled=asbool(_get_config("DD_AIO_PIKA_DISTRIBUTED_TRACING", default=False)),
    ),
)

if config._data_streams_enabled:
    # Importing from the integration side keeps DSM registration one-way.
    # ddtrace.internal.datastreams must not import this plugin and be imported
    # back by it, since that creates circular imports during package startup.
    import ddtrace.internal.datastreams.aio_pika  # noqa:F401,E402


def get_version() -> str:
    return getattr(aio_pika, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aio_pika": ">=9.0.0"}


def _extract_conn_attributes(instance: Any) -> dict[str, Any]:
    """Extract connection host/port from an aio-pika object.

    Chain: instance.channel (aio_pika.Channel) -> ._connection (aio_pika.Connection)
           -> .url (yarl.URL)
    """
    try:
        url = instance.channel._connection.url
        attributes: dict[str, Any] = {}
        if url.host:
            attributes[net.TARGET_HOST] = url.host
        if url.port:
            attributes[net.TARGET_PORT] = url.port
        return attributes
    except AttributeError:
        log.debug(
            "aio_pika: could not extract connection attributes from %r — "
            "the integration may be patching an unexpected object type",
            instance,
            exc_info=True,
        )
        return {}


async def traced_publish(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Trace Exchange.publish calls."""
    message = args[0] if args else kwargs.get("message")

    if message is None or not hasattr(message, "headers"):
        return await func(*args, **kwargs)

    if message.headers is None:
        message.headers = {}

    exchange_name = getattr(instance, "name", "") or ""
    routing_key = args[1] if len(args) > 1 else kwargs.get("routing_key", "") or ""
    conn_attributes = _extract_conn_attributes(instance)

    with core.context_with_event(
        MessagingPublishEvent(
            messaging_system=_MESSAGING_SYSTEM,
            # AIDEV-NOTE: Keep the exchange and routing key separate. In particular,
            # the default exchange must remain "" while still reporting that a
            # routing key was provided to DSM.
            destination=exchange_name,
            routing_key=routing_key,
            component=config.aio_pika.integration_name,
            integration_config=config.aio_pika,
            service=trace_utils.ext_service(None, config.aio_pika),
            headers=message.headers,
            body=getattr(message, "body", b"") or b"",
            distributed_tracing_enabled=config.aio_pika.distributed_tracing_enabled,
            attributes=conn_attributes,
        ),
        context_name_override=AioPikaEvents.PUBLISH.value,
    ):
        return await func(*args, **kwargs)


async def traced_consumer(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Trace the per-message consumer() function in aio_pika.queue.

    Signature: consumer(callback, msg, *, no_ack) -> Any
    """
    msg = args[1] if len(args) > 1 else kwargs.get("msg")
    destination = ""
    decoded_headers: dict[str, str] = {}
    body = b""

    if msg is not None:
        exchange_name = getattr(msg, "exchange", "") or ""
        routing_key = getattr(msg, "routing_key", "") or ""
        destination = exchange_name or routing_key
        body = getattr(msg, "body", b"") or b""

        try:
            raw = msg.header.properties.headers
            if raw:
                decoded_headers = trace_utils.decode_amqp_headers(raw)
        except AttributeError:
            log.debug(
                "aio_pika: could not extract headers from %r — "
                "the integration may be patching an unexpected message type",
                msg,
                exc_info=True,
            )

    with core.context_with_event(
        MessagingConsumeEvent(
            messaging_system=_MESSAGING_SYSTEM,
            destination=destination,
            component=config.aio_pika.integration_name,
            integration_config=config.aio_pika,
            service=trace_utils.ext_service(None, config.aio_pika),
            headers=decoded_headers,
            body=body,
            distributed_tracing_enabled=config.aio_pika.distributed_tracing_enabled,
        ),
        context_name_override=AioPikaEvents.CONSUME.value,
    ):
        return await func(*args, **kwargs)


async def traced_get(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Trace Queue.get calls.

    The underlying function is called first so we can extract distributed
    context from the result's headers and parent the span correctly.
    """
    start_ns = time_ns()
    queue_name = getattr(instance, "name", "")
    result = None
    func_error = None
    try:
        result = await func(*args, **kwargs)
    except Exception as e:
        func_error = e

    decoded_headers: dict[str, str] = {}
    body = b""
    if result is not None:
        raw = getattr(result, "headers", None)
        if raw:
            decoded_headers = trace_utils.decode_amqp_headers(raw)
        body = getattr(result, "body", b"") or b""

    with core.context_with_event(
        MessagingConsumeEvent(
            messaging_system=_MESSAGING_SYSTEM,
            destination=queue_name,
            component=config.aio_pika.integration_name,
            integration_config=config.aio_pika,
            service=trace_utils.ext_service(None, config.aio_pika),
            headers=decoded_headers,
            body=body,
            distributed_tracing_enabled=config.aio_pika.distributed_tracing_enabled,
            operation="receive",
            span_operation="get",
            start_ns=start_ns,
        ),
        context_name_override=AioPikaEvents.CONSUME.value,
    ):
        if func_error is not None:
            raise func_error

    return result


def _make_action_wrapper(operation: str) -> Callable[..., Any]:
    """Create a traced wrapper for IncomingMessage actions (ack, nack, reject)."""

    async def _traced_action(
        func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        exchange_name = getattr(instance, "exchange", "") or ""
        routing_key = getattr(instance, "routing_key", "") or ""
        destination = exchange_name or routing_key
        with core.context_with_event(
            MessagingActionEvent(
                messaging_system=_MESSAGING_SYSTEM,
                destination=destination,
                component=config.aio_pika.integration_name,
                integration_config=config.aio_pika,
                service=trace_utils.ext_service(None, config.aio_pika),
                operation=operation,
            ),
            context_name_override=AioPikaEvents.ACTION.value,
        ):
            return await func(*args, **kwargs)

    return _traced_action


def patch() -> None:
    if getattr(aio_pika, "_datadog_patch", False):
        return
    aio_pika._datadog_patch = True

    _w("aio_pika", "Exchange.publish", traced_publish)
    _w("aio_pika.queue", "consumer", traced_consumer)
    _w("aio_pika", "Queue.get", traced_get)
    _w("aio_pika", "IncomingMessage.ack", _make_action_wrapper("ack"))
    _w("aio_pika", "IncomingMessage.nack", _make_action_wrapper("nack"))
    _w("aio_pika", "IncomingMessage.reject", _make_action_wrapper("reject"))


def unpatch() -> None:
    if not getattr(aio_pika, "_datadog_patch", False):
        return
    aio_pika._datadog_patch = False

    _u(aio_pika.Exchange, "publish")
    _u(aio_pika.Queue, "get")
    _u(aio_pika.IncomingMessage, "ack")
    _u(aio_pika.IncomingMessage, "nack")
    _u(aio_pika.IncomingMessage, "reject")

    _u(aio_pika.queue, "consumer")
