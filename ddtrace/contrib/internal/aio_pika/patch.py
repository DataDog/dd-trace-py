from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import MutableMapping
from contextlib import asynccontextmanager
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
from time import time_ns
from typing import Any
from typing import Iterator
from typing import Optional
from typing import cast

import aio_pika

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.messaging import MessagingActionEvent
from ddtrace.contrib._events.messaging import MessagingProcessEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.contrib._events.messaging import MessagingReceiveEvent
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_MESSAGE_ID
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool


_MESSAGING_SYSTEM = "rabbitmq"
_PUBLISH = "rabbitmq.publish"
_GET = "rabbitmq.get"
_CONSUME = "rabbitmq.consume"

_EXCHANGE = "rabbitmq.exchange"
_QUEUE = "rabbitmq.queue"
_REDELIVERED = "rabbitmq.redelivered"
_ROUTING_KEY = "rabbitmq.routing_key"
_VHOST = "out.vhost"


config._add(  # type: ignore[no-untyped-call]
    "aio_pika",
    {
        "distributed_tracing": asbool(env.get("DD_AIO_PIKA_DISTRIBUTED_TRACING", default=False)),
    },
)

# Task-local identity of the IncomingMessage currently covered by a process span.
# Used only to suppress a nested message.process() span; it is not event data.
_PROCESSING_MESSAGE: ContextVar[Any] = ContextVar("ddtrace.aio_pika.processing_message", default=None)


def get_version() -> str:
    return str(getattr(aio_pika, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"aio_pika": ">=9.0.0"}


def _service() -> Optional[str]:
    return trace_utils.int_service(None, config.aio_pika)


def _operation(v0_name: str, direction: SpanDirection) -> str:
    return str(
        schematize_messaging_operation(  # type: ignore[operator]  # schema helper is selected at import
            v0_name, provider=_MESSAGING_SYSTEM, direction=direction
        )
    )


def _span_links_enabled() -> bool:
    return config.aio_pika.integration_name in config._propagation_as_span_links


def _string_headers(message: Any) -> dict[str, str]:
    headers = getattr(message, "headers", None)
    if not headers:
        return {}
    return {
        str(key): value.decode("utf-8", errors="ignore") if isinstance(value, (bytes, bytearray)) else str(value)
        for key, value in headers.items()
        if value is not None
    }


def _connection_tags(channel: Any) -> dict[str, str]:
    """Return non-sensitive connection facts from either aio-pika channel shape."""
    pending = [channel]
    seen: set[int] = set()
    url = None
    while pending:
        candidate = pending.pop(0)
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        url = getattr(candidate, "url", None)
        if url is not None:
            break
        for attribute in ("_connection", "connection"):
            try:
                nested = getattr(candidate, attribute, None)
            except Exception:
                nested = None
            if nested is not None:
                pending.append(nested)

    if url is None:
        return {}

    tags: dict[str, str] = {}
    host = getattr(url, "host", None)
    port = getattr(url, "port", None)
    path = getattr(url, "path", None)
    if host:
        tags[net.TARGET_HOST] = str(host)
    if port:
        tags[net.TARGET_PORT] = str(port)
    if path:
        tags[_VHOST] = path[1:] if path.startswith("/") and len(path) > 1 else path
    return tags


def _message_tags(message: Any) -> dict[str, str]:
    tags: dict[str, str] = {}
    exchange = getattr(message, "exchange", None)
    routing_key = getattr(message, "routing_key", None)
    message_id = getattr(message, "message_id", None)
    redelivered = getattr(message, "redelivered", None)
    if exchange is not None:
        tags[_EXCHANGE] = str(exchange)
    if routing_key is not None:
        tags[_ROUTING_KEY] = str(routing_key)
    if message_id is not None:
        tags[MESSAGING_MESSAGE_ID] = str(message_id)
    if redelivered is not None:
        tags[_REDELIVERED] = str(redelivered).lower()
    tags.update(_connection_tags(getattr(message, "channel", None)))
    return tags


def _message_destination(message: Any) -> str:
    return str(getattr(message, "exchange", None) or getattr(message, "routing_key", None) or "")


def _incoming_event_kwargs(message: Any) -> dict[str, Any]:
    return {
        "component": config.aio_pika.integration_name,
        "integration_config": config.aio_pika,
        "service": _service(),
        "request_headers": _string_headers(message),
        "messaging_system": _MESSAGING_SYSTEM,
        "propagation_as_span_links": _span_links_enabled(),
        "tags": _message_tags(message) if message is not None else {},
    }


def _receive_tags(instance: Any, message: Any, destination: str) -> dict[str, str]:
    tags = _message_tags(message) if message is not None else {}
    tags[_QUEUE] = destination
    queue = getattr(instance, "_amqp_queue", instance)
    tags.update(_connection_tags(getattr(queue, "channel", None)))
    return tags


async def _traced_publish(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    message = cast(Any, get_argument_value(args, kwargs, 0, "message"))
    routing_key = get_argument_value(args, kwargs, 1, "routing_key")
    exchange = str(getattr(instance, "name", ""))
    destination = exchange or str(routing_key)

    headers = getattr(message, "headers", None)
    if not isinstance(headers, MutableMapping):
        try:
            headers = dict(headers or {})
            message.headers = headers
        except (AttributeError, TypeError, ValueError):
            headers = None

    tags = {_EXCHANGE: exchange, _ROUTING_KEY: str(routing_key)}
    message_id = getattr(message, "message_id", None)
    if message_id is not None:
        tags[MESSAGING_MESSAGE_ID] = str(message_id)
    tags.update(_connection_tags(getattr(instance, "channel", None)))

    event = MessagingProducerEvent(
        operation=_operation(_PUBLISH, SpanDirection.OUTBOUND),
        component=config.aio_pika.integration_name,
        integration_config=config.aio_pika,
        service=_service(),
        distributed_headers=headers,
        messaging_system=_MESSAGING_SYSTEM,
        semantic_operation="send",
        destination=destination,
        tags=tags,
    )
    with core.context_with_event(event):
        return await wrapped(*args, **kwargs)


def _is_iterator_callback(callback: Any) -> bool:
    return (
        isinstance(getattr(callback, "__self__", None), aio_pika.queue.QueueIterator)
        and getattr(callback, "__name__", "") == "on_message"
    )


@contextmanager
def _mark_processing(message: Any) -> Iterator[None]:
    token = _PROCESSING_MESSAGE.set(message)
    try:
        yield
    finally:
        _PROCESSING_MESSAGE.reset(token)


async def _call_callback(callback: Callable[[Any], Any], message: Any) -> Any:
    result = callback(message)
    if inspect.isawaitable(result):
        return await result
    return result


def _traced_callback(callback: Callable[[Any], Any]) -> Callable[[Any], Awaitable[Any]]:
    @wraps(callback)
    async def traced(message: Any) -> Any:
        event = MessagingProcessEvent(
            operation=_operation(_CONSUME, SpanDirection.PROCESSING),
            semantic_operation="process",
            destination=_message_destination(message),
            **_incoming_event_kwargs(message),
        )
        with _mark_processing(message), core.context_with_event(event):
            return await _call_callback(callback, message)

    return traced


async def _traced_consumer(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    callback = cast(Callable[[Any], Any], get_argument_value(args, kwargs, 0, "callback"))
    if _is_iterator_callback(callback):
        return await wrapped(*args, **kwargs)
    args, kwargs = set_argument_value(args, kwargs, 0, "callback", _traced_callback(callback))
    return await wrapped(*args, **kwargs)


async def _trace_receive(
    wrapped: Callable[..., Awaitable[Any]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    destination: str,
) -> Any:
    start_ns = time_ns()
    try:
        message = await wrapped(*args, **kwargs)
    except StopAsyncIteration:
        event = MessagingReceiveEvent(
            operation=_operation(_GET, SpanDirection.INBOUND),
            semantic_operation="receive",
            destination=destination,
            start_ns=start_ns,
            **_incoming_event_kwargs(None),
        )
        event.tags = _receive_tags(instance, None, destination)
        with core.context_with_event(event):
            pass
        raise
    except BaseException:
        event = MessagingReceiveEvent(
            operation=_operation(_GET, SpanDirection.INBOUND),
            semantic_operation="receive",
            destination=destination,
            start_ns=start_ns,
            **_incoming_event_kwargs(None),
        )
        event.tags = _receive_tags(instance, None, destination)
        with core.context_with_event(event):
            raise

    event = MessagingReceiveEvent(
        operation=_operation(_GET, SpanDirection.INBOUND),
        semantic_operation="receive",
        destination=destination,
        start_ns=start_ns,
        **_incoming_event_kwargs(message),
    )
    event.tags = _receive_tags(instance, message, destination)
    with core.context_with_event(event):
        pass
    return message


async def _traced_get(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    destination = str(getattr(instance, "name", ""))
    return await _trace_receive(wrapped, instance, args, kwargs, destination)


async def _traced_anext(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    destination = str(getattr(getattr(instance, "_amqp_queue", None), "name", ""))
    return await _trace_receive(wrapped, instance, args, kwargs, destination)


@asynccontextmanager
async def _traced_process_context(original: Any, message: Any) -> AsyncIterator[Any]:
    if _PROCESSING_MESSAGE.get() is message:
        async with original as incoming:
            yield incoming
        return

    event = MessagingProcessEvent(
        operation=_operation(_CONSUME, SpanDirection.PROCESSING),
        semantic_operation="process",
        destination=_message_destination(message),
        **_incoming_event_kwargs(message),
    )
    with _mark_processing(message), core.context_with_event(event):
        async with original as incoming:
            yield incoming


def _traced_process(wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return _traced_process_context(wrapped(*args, **kwargs), instance)


def _action_wrapper(action: str) -> Callable[..., Awaitable[Any]]:
    async def traced(
        wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        destination = _message_destination(instance)
        event = MessagingActionEvent(
            operation=f"rabbitmq.{action}",
            action=action,
            semantic_operation=action,
            destination=destination,
            component=config.aio_pika.integration_name,
            integration_config=config.aio_pika,
            service=_service(),
            messaging_system=_MESSAGING_SYSTEM,
            tags=_message_tags(instance),
        )
        with core.context_with_event(event):
            return await wrapped(*args, **kwargs)

    return traced


def patch() -> None:
    if getattr(aio_pika, "_datadog_patch", False):
        return

    wrap("aio_pika.exchange", "Exchange.publish", _traced_publish)
    wrap("aio_pika.queue", "consumer", _traced_consumer)
    wrap("aio_pika.queue", "Queue.get", _traced_get)
    wrap("aio_pika.queue", "QueueIterator.__anext__", _traced_anext)

    if "__anext__" in aio_pika.robust_queue.RobustQueueIterator.__dict__:
        wrap("aio_pika.robust_queue", "RobustQueueIterator.__anext__", _traced_anext)

    wrap("aio_pika.message", "IncomingMessage.process", _traced_process)
    for action in ("ack", "nack", "reject"):
        wrap("aio_pika.message", f"IncomingMessage.{action}", _action_wrapper(action))

    aio_pika._datadog_patch = True


def unpatch() -> None:
    if not getattr(aio_pika, "_datadog_patch", False):
        return

    unwrap(aio_pika.exchange.Exchange, "publish")
    unwrap(aio_pika.queue, "consumer")
    unwrap(aio_pika.queue.Queue, "get")
    unwrap(aio_pika.queue.QueueIterator, "__anext__")
    if "__anext__" in aio_pika.robust_queue.RobustQueueIterator.__dict__:
        unwrap(aio_pika.robust_queue.RobustQueueIterator, "__anext__")
    unwrap(aio_pika.message.IncomingMessage, "process")
    for action in ("ack", "nack", "reject"):
        unwrap(aio_pika.message.IncomingMessage, action)

    aio_pika._datadog_patch = False
