import copy
from typing import Any
from typing import Callable
from typing import Iterator
from typing import Optional

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import BasicProperties
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.messaging import MessagingConsumeEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.ext import net
from ddtrace.ext import pika as pikax
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings import env
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


config._add(
    "pika",
    dict(
        _default_service=schematize_service_name(pikax.SERVICE),
        distributed_tracing_enabled=asbool(env.get("DD_PIKA_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version() -> str:
    return str(getattr(pika, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"pika": ">=1.0.0"}


def _body_length(body: Any) -> int:
    return len(body) if body is not None else 0


def _conn_tags(channel: Any) -> dict[str, Any]:
    params = getattr(getattr(channel, "connection", None), "params", None)
    if params is None:
        return {}
    return {
        net.TARGET_HOST: params.host,
        net.TARGET_PORT: params.port,
        pikax.VHOST: params.virtual_host,
    }


def _publish_properties(properties: Optional[BasicProperties]) -> BasicProperties:
    if properties is None:
        return BasicProperties(headers={})
    copied = copy.copy(properties)
    copied.headers = dict(properties.headers or {})
    return copied


def _traced_basic_publish(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    exchange = get_argument_value(args, kwargs, 0, "exchange", optional=True) or ""
    routing_key = get_argument_value(args, kwargs, 1, "routing_key", optional=True) or ""
    body = get_argument_value(args, kwargs, 2, "body", optional=True)
    properties = _publish_properties(get_argument_value(args, kwargs, 3, "properties", optional=True))
    args, kwargs = set_argument_value(args, kwargs, 3, "properties", properties, override_unset=True)

    event = MessagingProducerEvent(
        messaging_operation=pikax.PUBLISH_NAME,
        provider="pika",
        distributed_headers=properties.headers,
        component=config.pika.integration_name,
        integration_config=config.pika,
        service=trace_utils.int_service(None, config.pika),
        resource=exchange or routing_key,
        tags={
            pikax.EXCHANGE: exchange,
            pikax.ROUTING_KEY: routing_key,
            **_conn_tags(instance),
        },
    )
    with core.context_with_event(event) as ctx:
        span = span_from_context(ctx)
        span._set_attribute(pikax.BODY_LEN, _body_length(body))
        core.dispatch("pika.basic_publish.pre", (exchange, routing_key, body, properties.headers, span))
        return wrapped(*args, **kwargs)


def _receive_event(channel: Any, queue: str, method: Any, properties: Any, body: Any, method_name: str) -> Any:
    headers = (getattr(properties, "headers", None) if properties is not None else None) or {}
    return MessagingConsumeEvent(
        messaging_operation=pikax.RECEIVE_NAME,
        provider="pika",
        direction=SpanDirection.PROCESSING,
        request_headers=headers,
        activate_distributed_headers=True,
        component=config.pika.integration_name,
        integration_config=config.pika,
        service=trace_utils.int_service(None, config.pika),
        resource=queue,
        tags={
            pikax.EXCHANGE: getattr(method, "exchange", "") or "",
            pikax.ROUTING_KEY: getattr(method, "routing_key", "") or "",
            pikax.QUEUE: queue,
            pikax.METHOD: method_name,
            **_conn_tags(channel),
        },
    )


def _dispatch_receive(ctx: Any, queue: str, properties: Any, body: Any) -> None:
    headers = (getattr(properties, "headers", None) if properties is not None else None) or {}
    span = span_from_context(ctx)
    span._set_attribute(pikax.BODY_LEN, _body_length(body))
    core.dispatch("pika.receive.pre", (queue, headers, body, span))


def _traced_callback(queue: str, callback: Callable[..., Any], method_name: str) -> Callable[..., Any]:
    def traced(channel: Any, method: Any, properties: Optional[BasicProperties], body: Any) -> Any:
        with core.context_with_event(_receive_event(channel, queue, method, properties, body, method_name)) as ctx:
            _dispatch_receive(ctx, queue, properties, body)
            return callback(channel, method, properties, body)

    return traced


def _is_blocking_channel_callback(callback: Any) -> bool:
    return isinstance(getattr(callback, "__self__", None), BlockingChannel)


def _traced_basic_consume(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    queue = get_argument_value(args, kwargs, 0, "queue", optional=True) or ""
    callback = get_argument_value(args, kwargs, 1, "on_message_callback", optional=True)
    if callable(callback) and not _is_blocking_channel_callback(callback):
        args, kwargs = set_argument_value(
            args, kwargs, 1, "on_message_callback", _traced_callback(queue, callback, "basic_consume")
        )
    return wrapped(*args, **kwargs)


def _traced_basic_get(wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    queue = get_argument_value(args, kwargs, 0, "queue", optional=True) or ""
    callback = get_argument_value(args, kwargs, 1, "callback", optional=True)
    if callable(callback):
        args, kwargs = set_argument_value(args, kwargs, 1, "callback", _traced_callback(queue, callback, "basic_get"))
    return wrapped(*args, **kwargs)


def _traced_blocking_consume(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Iterator[Any]:
    queue = get_argument_value(args, kwargs, 0, "queue", optional=True) or ""
    consumer = wrapped(*args, **kwargs)
    try:
        for item in consumer:
            if item is None or item[0] is None:
                yield item
                continue
            method, properties, body = item
            with core.context_with_event(_receive_event(instance, queue, method, properties, body, "consume")) as ctx:
                _dispatch_receive(ctx, queue, properties, body)
                try:
                    yield item
                except GeneratorExit:
                    ctx.dispatch_ended_event()
                    raise
    finally:
        consumer.close()


def patch() -> None:
    if getattr(pika, "_datadog_patch", False):
        return
    pika._datadog_patch = True

    _w("pika.channel", "Channel.basic_publish", _traced_basic_publish)
    _w("pika.channel", "Channel.basic_consume", _traced_basic_consume)
    _w("pika.channel", "Channel.basic_get", _traced_basic_get)
    _w("pika.adapters.blocking_connection", "BlockingChannel.basic_consume", _traced_basic_consume)
    _w("pika.adapters.blocking_connection", "BlockingChannel.consume", _traced_blocking_consume)


def unpatch() -> None:
    if not getattr(pika, "_datadog_patch", False):
        return
    pika._datadog_patch = False

    _u(pika.channel.Channel, "basic_publish")
    _u(pika.channel.Channel, "basic_consume")
    _u(pika.channel.Channel, "basic_get")
    _u(BlockingChannel, "basic_consume")
    _u(BlockingChannel, "consume")
