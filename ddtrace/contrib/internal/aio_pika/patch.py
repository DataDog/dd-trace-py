import asyncio
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextlib import contextmanager
from contextvars import ContextVar
import copy
from functools import wraps
import inspect
from time import time_ns
from typing import Any
from typing import Iterator
from typing import Optional
from typing import cast

import aio_pika

from ddtrace import config
from ddtrace._trace.context import Context
from ddtrace._trace.events import TracingEvent
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.messaging import MessagingConsumeEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_MESSAGE_ID
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


_PROVIDER = "rabbitmq"
_PUBLISH = "rabbitmq.publish"
_GET = "rabbitmq.get"
_CONSUME = "rabbitmq.consume"

_EXCHANGE = "rabbitmq.exchange"
_QUEUE = "rabbitmq.queue"
_REDELIVERED = "rabbitmq.redelivered"
_ROUTING_KEY = "rabbitmq.routing_key"
# Same tag as the kombu integration, so spans from both AMQP clients line up.
_VHOST = "out.vhost"

log = get_logger(__name__)

# aio-pika 10 overrides __anext__ on RobustQueueIterator; 9.x inherits it from QueueIterator.
_ROBUST_ITERATOR_HAS_ANEXT = "__anext__" in aio_pika.robust_queue.RobustQueueIterator.__dict__
# Queue.consume adapts the callback with this helper (and warns about sync callbacks); older releases lack it.
_ENSURE_AWAITABLE: Optional[Callable[[Any], Any]] = getattr(getattr(aio_pika, "tools", None), "ensure_awaitable", None)


config._add(  # type: ignore[no-untyped-call]
    "aio_pika",
    {
        "distributed_tracing_enabled": asbool(_get_config("DD_AIO_PIKA_DISTRIBUTED_TRACING", default=False)),
    },
)

# Task-local identity of the IncomingMessage currently covered by a process span.
# Used only to suppress a nested message.process() span inside a traced callback.
_PROCESSING_MESSAGE: ContextVar[Any] = ContextVar("ddtrace.aio_pika.processing_message", default=None)


def get_version() -> str:
    return str(getattr(aio_pika, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"aio_pika": ">=9.0.0"}


def _service() -> Optional[str]:
    return trace_utils.int_service(None, config.aio_pika)


def _distributed_tracing_enabled() -> bool:
    return trace_utils.distributed_tracing_enabled(config.aio_pika)


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


def _connection_tags(owner: Any) -> dict[str, str]:
    """Return host, port and vhost from the URL of owner's connection, without credentials.

    Messages, and every object on aio-pika 9.x, hold an aiormq channel (connection attribute). Exchanges and
    queues on aio-pika 10 hold an aio_pika.Channel (_connection attribute). aio-pika 10 raises from
    IncomingMessage.channel once the channel is closed, so either failure yields no tags.
    """
    try:
        channel = owner.channel
        connection = getattr(channel, "connection", None) or getattr(channel, "_connection", None)
        url = getattr(connection, "url", None)
    except (AttributeError, aio_pika.exceptions.ChannelInvalidStateError):
        log.debug("aio_pika: could not read connection details from %r", owner, exc_info=True)
        return {}
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
    tags.update(_connection_tags(message))
    return tags


def _message_destination(message: Any) -> str:
    return str(getattr(message, "exchange", None) or getattr(message, "routing_key", None) or "")


def _resource(name: str) -> Optional[str]:
    # Server-named queues (amq.gen-*, direct reply-to) are unique per consumer, so they would explode
    # resource cardinality; the span name is used instead.
    if not name or name.startswith(("amq.gen-", "amq.rabbitmq.reply-to")):
        return None
    return name


def _semantic_tags(operation: str, destination: str) -> dict[str, str]:
    return {
        MESSAGING_SYSTEM: _PROVIDER,
        MESSAGING_OPERATION: operation,
        MESSAGING_DESTINATION_NAME: destination,
    }


def _consume_event(
    message: Any,
    operation_name: str,
    direction: SpanDirection,
    destination: str,
    tags: dict[str, str],
    activate: bool = True,
) -> MessagingConsumeEvent:
    """Build a consume event whose parent or span link comes from the message headers.

    An activated span takes its parent by activating the producer context, like kombu. A span that is not
    activated is parented on the producer directly, so the producer context never becomes active.
    """
    headers = _string_headers(message)
    links_mode = _span_links_enabled()
    event = MessagingConsumeEvent(
        messaging_operation=operation_name,
        provider=_PROVIDER,
        direction=direction,
        component=config.aio_pika.integration_name,
        integration_config=config.aio_pika,
        service=_service(),
        resource=_resource(destination),
        request_headers=headers,
        activate=activate,
        activate_distributed_headers=activate and not links_mode,
        tags=tags,
    )
    if links_mode or not activate:
        link = _attach_producer(event, message, headers)
        if link is not None:
            event.span_links.append(link)
    return event


def _copy_for_injection(message: Any) -> Optional[Any]:
    """Return a copy of message with its own headers dict, so injection never mutates the caller's message.

    aio-pika's publish reads only the message body and properties, so the copy is equivalent on the wire.
    publish accepts any AbstractMessage; one that cannot be copied or has read-only headers is published
    unchanged, without propagation.
    """
    try:
        outgoing = copy.copy(message)
        outgoing.headers = dict(getattr(message, "headers", None) or {})
    except (AttributeError, TypeError):
        log.debug("aio_pika: could not copy %r for header injection; publishing it unchanged", message, exc_info=True)
        return None
    return outgoing


async def _traced_publish(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    message = get_argument_value(args, kwargs, 0, "message")
    routing_key = get_argument_value(args, kwargs, 1, "routing_key")
    exchange = str(getattr(instance, "name", ""))
    destination = exchange or str(routing_key)

    # DSM writes its pathway header even when trace propagation is off, so either one needs a copy to inject into.
    distributed_tracing = _distributed_tracing_enabled()
    data_streams = config._data_streams_enabled
    headers = None
    outgoing_headers = None
    if distributed_tracing or data_streams:
        outgoing = _copy_for_injection(message)
        if outgoing is not None:
            outgoing_headers = outgoing.headers
            args, kwargs = set_argument_value(args, kwargs, 0, "message", outgoing)
            if distributed_tracing:
                headers = outgoing_headers

    tags = {_EXCHANGE: exchange, _ROUTING_KEY: str(routing_key), **_semantic_tags("send", destination)}
    message_id = getattr(message, "message_id", None)
    if message_id is not None:
        tags[MESSAGING_MESSAGE_ID] = str(message_id)
    tags.update(_connection_tags(instance))

    event = MessagingProducerEvent(
        messaging_operation=_PUBLISH,
        provider=_PROVIDER,
        component=config.aio_pika.integration_name,
        integration_config=config.aio_pika,
        service=_service(),
        resource=_resource(destination),
        distributed_headers=headers,
        tags=tags,
    )
    with core.context_with_event(event) as ctx:
        if data_streams:
            core.dispatch(
                "aio_pika.publish.start",
                (exchange, str(routing_key), outgoing_headers, getattr(message, "body", None), span_from_context(ctx)),
            )
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


def _process_event(message: Any) -> MessagingConsumeEvent:
    destination = _message_destination(message)
    tags = {**_message_tags(message), **_semantic_tags("process", destination)}
    return _consume_event(message, _CONSUME, SpanDirection.PROCESSING, destination, tags)


async def _call_callback(callback: Callable[[Any], Any], message: Any) -> Any:
    result = callback(message)
    if inspect.isawaitable(result):
        return await result
    return result


def _dispatch_receive(queue: str, message: Any, ctx: Any) -> None:
    """Notify DSM that the application received message from queue."""
    if not config._data_streams_enabled:
        return
    # An unnamed queue falls back to the routing key, which names the queue on the default exchange.
    queue_name = queue or str(getattr(message, "routing_key", None) or "")
    core.dispatch("aio_pika.receive.post", (queue_name, message, span_from_context(ctx)))


def _traced_callback(callback: Callable[[Any], Any], queue: str) -> Callable[[Any], Awaitable[Any]]:
    """Wrap a consumer callback once per consume; the AMQP delivery itself does not name its queue."""

    @wraps(callback)
    async def traced(message: Any) -> Any:
        with _mark_processing(message), core.context_with_event(_process_event(message)) as ctx:
            _dispatch_receive(queue, message, ctx)
            return await _call_callback(callback, message)

    return traced


async def _traced_queue_consume(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    callback = cast(Callable[[Any], Any], get_argument_value(args, kwargs, 0, "callback"))
    if _is_iterator_callback(callback):
        # The iterator path is traced when the application pulls the message in __anext__.
        return await wrapped(*args, **kwargs)
    if _ENSURE_AWAITABLE is not None:
        # Apply aio-pika's own adapter first, so a sync callback still gets its deprecation warning.
        callback = _ENSURE_AWAITABLE(callback)
    traced = _traced_callback(callback, str(getattr(instance, "name", "")))
    args, kwargs = set_argument_value(args, kwargs, 0, "callback", traced)
    return await wrapped(*args, **kwargs)


def _attach_producer(event: TracingEvent, message: Any, headers: Optional[dict[str, str]] = None) -> Optional[Context]:
    """Parent event on the producer context in the message headers, or return that context to link.

    In span-links mode the producer context is returned for the caller to link; otherwise event is parented on
    it. The producer context is never activated. headers, when given, are the message's already-decoded headers.
    """
    if not _distributed_tracing_enabled():
        return None
    if headers is None:
        headers = _string_headers(message)
    if not headers:
        return None
    # HTTPPropagator.extract has no return annotation in the legacy propagation API.
    producer = cast(Context, HTTPPropagator.extract(headers))  # type: ignore[no-untyped-call]
    if not (producer.trace_id and producer.span_id):
        return None
    if _span_links_enabled():
        return producer
    event.distributed_context = producer
    event.use_active_context = False
    return None


def _receive_event(instance: Any, message: Any, destination: str) -> MessagingConsumeEvent:
    if message is not None:
        tags = _message_tags(message)
    else:
        tags = _connection_tags(getattr(instance, "_amqp_queue", instance))
    tags[_QUEUE] = destination
    tags.update(_semantic_tags("receive", destination))
    # The receive span has no children; activating it, or the producer context, would leak into the caller.
    return _consume_event(message, _GET, SpanDirection.INBOUND, destination, tags, activate=False)


async def _receive(
    wrapped: Callable[..., Awaitable[Any]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    destination: str,
) -> Any:
    start_ns = time_ns()
    try:
        message = await wrapped(*args, **kwargs)
    except (StopAsyncIteration, asyncio.CancelledError):
        # End of iteration and a cancelled wait (for example at shutdown) are not receives.
        raise
    except BaseException:
        with core.context_with_event(_receive_event(instance, None, destination)) as ctx:
            span_from_context(ctx).start_ns = start_ns
            raise

    with core.context_with_event(_receive_event(instance, message, destination)) as ctx:
        span_from_context(ctx).start_ns = start_ns
        # get(fail=False) returns None on an empty queue, which is not a receive for DSM. A later
        # message.process() records no checkpoint, so a pulled message is counted once.
        if message is not None:
            _dispatch_receive(destination, message, ctx)
    return message


async def _traced_get(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    destination = str(getattr(instance, "name", ""))
    return await _receive(wrapped, instance, args, kwargs, destination)


async def _traced_anext(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    destination = str(getattr(getattr(instance, "_amqp_queue", None), "name", ""))
    return await _receive(wrapped, instance, args, kwargs, destination)


@asynccontextmanager
async def _traced_process_context(original: Any, message: Any) -> AsyncIterator[Any]:
    if _PROCESSING_MESSAGE.get() is message:
        async with original as incoming:
            yield incoming
        return

    previous = tracer.context_provider.active()
    try:
        with _mark_processing(message), core.context_with_event(_process_event(message)):
            async with original as incoming:
                yield incoming
    finally:
        # A span parented on a remote producer context cannot restore the span it displaced when it
        # finishes; reactivate the caller's context, as the kafka consume subscriber does.
        tracer.context_provider.activate(previous)


def _traced_process(wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return _traced_process_context(wrapped(*args, **kwargs), instance)


def _action_wrapper(action: str) -> Callable[..., Awaitable[Any]]:
    async def traced(
        wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        destination = _message_destination(instance)
        event = TracingEvent.create(
            component=config.aio_pika.integration_name,
            integration_config=config.aio_pika,
            operation_name=f"rabbitmq.{action}",
            span_type=SpanTypes.WORKER,
            span_kind=SpanKind.CLIENT,
            service=_service(),
            resource=_resource(destination),
            tags={**_message_tags(instance), **_semantic_tags(action, destination)},
        )
        # Settling a pulled message outside process() has no active span; parent it on the producer (or link
        # it in span-links mode) instead of starting an unconnected trace. The context is not activated.
        link = _attach_producer(event, instance) if tracer.context_provider.active() is None else None
        with core.context_with_event(event) as ctx:
            if link is not None:
                # The generic span event carries no links, so the producer is linked on the span itself.
                span_from_context(ctx).link_span(link)
            return await wrapped(*args, **kwargs)

    return traced


def patch() -> None:
    if getattr(aio_pika, "_datadog_patch", False):
        return

    wrap("aio_pika.exchange", "Exchange.publish", _traced_publish)
    wrap("aio_pika.queue", "Queue.consume", _traced_queue_consume)
    wrap("aio_pika.queue", "Queue.get", _traced_get)
    wrap("aio_pika.queue", "QueueIterator.__anext__", _traced_anext)

    if _ROBUST_ITERATOR_HAS_ANEXT:
        wrap("aio_pika.robust_queue", "RobustQueueIterator.__anext__", _traced_anext)

    wrap("aio_pika.message", "IncomingMessage.process", _traced_process)
    for action in ("ack", "nack", "reject"):
        wrap("aio_pika.message", f"IncomingMessage.{action}", _action_wrapper(action))

    aio_pika._datadog_patch = True


def unpatch() -> None:
    if not getattr(aio_pika, "_datadog_patch", False):
        return

    unwrap(aio_pika.exchange.Exchange, "publish")
    unwrap(aio_pika.queue.Queue, "consume")
    unwrap(aio_pika.queue.Queue, "get")
    unwrap(aio_pika.queue.QueueIterator, "__anext__")
    if _ROBUST_ITERATOR_HAS_ANEXT:
        unwrap(aio_pika.robust_queue.RobustQueueIterator, "__anext__")
    unwrap(aio_pika.message.IncomingMessage, "process")
    for action in ("ack", "nack", "reject"):
        unwrap(aio_pika.message.IncomingMessage, action)

    aio_pika._datadog_patch = False
