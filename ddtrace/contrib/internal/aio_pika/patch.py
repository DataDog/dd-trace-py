from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import MutableMapping
from functools import wraps
import inspect
import sys
from time import time_ns
from typing import Any
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
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool


_COMPONENT = "aio_pika"
_MESSAGING_SYSTEM = "rabbitmq"
# AIDEV-NOTE: Counts are keyed by message identity because IncomingMessage does not
# provide a stable hash and concurrent callbacks may each enter message.process().
_PROCESSING_MESSAGES: dict[int, int] = {}
_PROCESS_CONTEXTS: dict[int, Optional[tuple[Any, int]]] = {}

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


def get_version() -> str:
    return str(getattr(aio_pika, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"aio_pika": ">=9.0.0"}


def _service() -> Optional[str]:
    return trace_utils.int_service(None, config.aio_pika)


def _span_links_enabled() -> bool:
    return _COMPONENT in config._propagation_as_span_links


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
        "component": _COMPONENT,
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
        operation="rabbitmq.publish",
        resource="rabbitmq.publish",
        component=_COMPONENT,
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


def _enter_callback(message: Any) -> None:
    identity = id(message)
    _PROCESSING_MESSAGES[identity] = _PROCESSING_MESSAGES.get(identity, 0) + 1


def _exit_callback(message: Any) -> None:
    identity = id(message)
    remaining = _PROCESSING_MESSAGES.get(identity, 0) - 1
    if remaining > 0:
        _PROCESSING_MESSAGES[identity] = remaining
    else:
        _PROCESSING_MESSAGES.pop(identity, None)


async def _call_callback(callback: Callable[[Any], Any], message: Any) -> Any:
    result = callback(message)
    if inspect.isawaitable(result):
        return await result
    return result


def _traced_callback(callback: Callable[[Any], Any]) -> Callable[[Any], Awaitable[Any]]:
    @wraps(callback)
    async def traced(message: Any) -> Any:
        _enter_callback(message)
        try:
            event = MessagingProcessEvent(
                operation="rabbitmq.consume",
                resource="rabbitmq.consume",
                semantic_operation="process",
                destination=_message_destination(message),
                **_incoming_event_kwargs(message),
            )
            with core.context_with_event(event):
                return await _call_callback(callback, message)
        finally:
            _exit_callback(message)

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
            operation="rabbitmq.get",
            resource="rabbitmq.get",
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
            operation="rabbitmq.get",
            resource="rabbitmq.get",
            semantic_operation="receive",
            destination=destination,
            start_ns=start_ns,
            **_incoming_event_kwargs(None),
        )
        event.tags = _receive_tags(instance, None, destination)
        with core.context_with_event(event):
            raise

    event = MessagingReceiveEvent(
        operation="rabbitmq.get",
        resource="rabbitmq.get",
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


async def _traced_process_enter(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    message = instance.message
    if _PROCESSING_MESSAGES.get(id(message), 0):
        _PROCESS_CONTEXTS[id(instance)] = None
        try:
            return await wrapped(*args, **kwargs)
        except BaseException:
            _PROCESS_CONTEXTS.pop(id(instance), None)
            raise

    destination = _message_destination(message)
    event = MessagingProcessEvent(
        operation="rabbitmq.consume",
        resource="rabbitmq.consume",
        semantic_operation="process",
        destination=destination,
        **_incoming_event_kwargs(message),
    )
    context_manager = core.context_with_event(event)
    context_manager.__enter__()
    _PROCESS_CONTEXTS[id(instance)] = (context_manager, id(message))
    try:
        return await wrapped(*args, **kwargs)
    except BaseException:
        _PROCESS_CONTEXTS.pop(id(instance), None)
        context_manager.__exit__(*sys.exc_info())
        raise


async def _traced_process_exit(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    stored = _PROCESS_CONTEXTS.pop(id(instance), None)
    if stored is None:
        return await wrapped(*args, **kwargs)

    context_manager, _ = stored
    try:
        result = await wrapped(*args, **kwargs)
    except BaseException:
        context_manager.__exit__(*sys.exc_info())
        raise
    else:
        exc_type = get_argument_value(args, kwargs, 0, "exc_type", optional=True)
        exc_val = get_argument_value(args, kwargs, 1, "exc_val", optional=True)
        exc_tb = get_argument_value(args, kwargs, 2, "exc_tb", optional=True)
        context_manager.__exit__(exc_type, exc_val, exc_tb)
        return result


def _action_wrapper(action: str) -> Callable[..., Awaitable[Any]]:
    async def traced(
        wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        destination = _message_destination(instance)
        event = MessagingActionEvent(
            operation=f"rabbitmq.{action}",
            resource=f"rabbitmq.{action}",
            action=action,
            semantic_operation=action,
            destination=destination,
            component=_COMPONENT,
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

    from aio_pika.robust_queue import RobustQueueIterator

    if "__anext__" in RobustQueueIterator.__dict__:
        wrap("aio_pika.robust_queue", "RobustQueueIterator.__anext__", _traced_anext)

    wrap("aio_pika.message", "ProcessContext.__aenter__", _traced_process_enter)
    wrap("aio_pika.message", "ProcessContext.__aexit__", _traced_process_exit)
    for action in ("ack", "nack", "reject"):
        wrap("aio_pika.message", f"IncomingMessage.{action}", _action_wrapper(action))

    aio_pika._datadog_patch = True


def unpatch() -> None:
    if not getattr(aio_pika, "_datadog_patch", False):
        return

    from aio_pika.exchange import Exchange
    from aio_pika.message import IncomingMessage
    from aio_pika.message import ProcessContext
    from aio_pika.queue import Queue
    from aio_pika.queue import QueueIterator
    from aio_pika.robust_queue import RobustQueueIterator

    unwrap(Exchange, "publish")
    unwrap(aio_pika.queue, "consumer")
    unwrap(Queue, "get")
    unwrap(QueueIterator, "__anext__")
    if "__anext__" in RobustQueueIterator.__dict__:
        unwrap(RobustQueueIterator, "__anext__")
    unwrap(ProcessContext, "__aenter__")
    unwrap(ProcessContext, "__aexit__")
    for action in ("ack", "nack", "reject"):
        unwrap(IncomingMessage, action)

    _PROCESSING_MESSAGES.clear()
    _PROCESS_CONTEXTS.clear()
    aio_pika._datadog_patch = False
