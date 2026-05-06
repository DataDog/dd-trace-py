"""Per-message middleware that the FastStream integration registers on every
broker. Implements FastStream's :class:`BrokerMiddleware` protocol.

Most of this file is mechanical attribute extraction per broker driver
(``raw_message`` exposes broker-specific fields). The dispatch is done via a
simple dict keyed by the detected ``messaging.system`` value rather than a
class hierarchy, so each broker is one tiny pair of functions.
"""
from typing import TYPE_CHECKING
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Tuple

from faststream._internal.middlewares import BaseMiddleware

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_MESSAGE_ID
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


if TYPE_CHECKING:
    from faststream._internal.context.repository import ContextRepo
    from faststream.message import StreamMessage
    from faststream.response import PublishCommand


# AIDEV-NOTE: messaging.system value is OTel-aligned. Walking the broker class
# MRO so that broker subclasses inherit their parent's mapping.
_BROKER_TO_MESSAGING_SYSTEM: Dict[str, str] = {
    "KafkaBroker": "kafka",
    "ConfluentBroker": "kafka",
    "RabbitBroker": "rabbitmq",
    "NatsBroker": "nats",
    "RedisBroker": "redis",
    "MQTTBroker": "mqtt",
}


def detect_messaging_system(broker: Any) -> str:
    """Return an OTel ``messaging.system`` value for a FastStream broker."""
    for cls in type(broker).__mro__:
        name = cls.__name__
        if name in _BROKER_TO_MESSAGING_SYSTEM:
            return _BROKER_TO_MESSAGING_SYSTEM[name]
    return "faststream"


_TagsResult = Tuple[Optional[str], Dict[str, Any]]


def _kafka_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    tags: Dict[str, Any] = {}
    topic = getattr(raw, "topic", None)
    partition = getattr(raw, "partition", None)
    offset = getattr(raw, "offset", None)
    key = getattr(raw, "key", None)
    if partition is not None:
        tags["messaging.kafka.partition"] = partition
    if offset is not None:
        tags["messaging.kafka.message.offset"] = offset
    if key is not None:
        tags["messaging.kafka.message.key"] = key
    return topic, tags


def _kafka_publish(cmd: "PublishCommand") -> _TagsResult:
    tags: Dict[str, Any] = {}
    partition = getattr(cmd, "partition", None)
    key = getattr(cmd, "key", None)
    if partition is not None:
        tags["messaging.kafka.destination.partition"] = partition
    if key is not None:
        tags["messaging.kafka.message.key"] = key
    return getattr(cmd, "destination", None), tags


def _rabbit_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    exchange = getattr(raw, "exchange", "") or "default"
    routing_key = getattr(raw, "routing_key", "") or ""
    delivery_tag = getattr(raw, "delivery_tag", None)
    tags: Dict[str, Any] = {
        "messaging.rabbitmq.destination.routing_key": routing_key,
    }
    if delivery_tag is not None:
        tags["messaging.rabbitmq.message.delivery_tag"] = delivery_tag
    return f"{exchange}.{routing_key}" if routing_key else exchange, tags


def _rabbit_publish(cmd: "PublishCommand") -> _TagsResult:
    exchange_obj = getattr(cmd, "exchange", None)
    exchange = getattr(exchange_obj, "name", None) or "default"
    routing_key = getattr(cmd, "destination", "") or ""
    tags: Dict[str, Any] = {
        "messaging.rabbitmq.destination.routing_key": routing_key,
    }
    return f"{exchange}.{routing_key}" if routing_key else exchange, tags


def _nats_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    if isinstance(raw, (list, tuple)) and raw:
        first = raw[0]
        return getattr(first, "subject", None), {"messaging.batch.message_count": len(raw)}
    return getattr(raw, "subject", None), {}


def _nats_publish(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}


def _redis_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    if isinstance(raw, dict):
        destination = raw.get("channel") or raw.get("list") or raw.get("stream")
        tags: Dict[str, Any] = {}
        if str(raw.get("type", "")).startswith("b") and isinstance(raw.get("data"), (list, tuple)):
            tags["messaging.batch.message_count"] = len(raw["data"])
        return destination, tags
    return None, {}


def _redis_publish(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}


def _mqtt_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    return getattr(msg.raw_message, "topic", None), {}


def _mqtt_publish(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}


def _generic_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = getattr(msg, "raw_message", None)
    if raw is None:
        return None, {}
    for attr in ("topic", "subject", "channel", "exchange"):
        val = getattr(raw, attr, None)
        if val:
            return val if isinstance(val, str) else str(val), {}
    return None, {}


def _generic_publish(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}


_BROKER_HANDLERS: Dict[
    str,
    Tuple[
        Callable[["StreamMessage[Any]"], _TagsResult],
        Callable[["PublishCommand"], _TagsResult],
    ],
] = {
    "kafka": (_kafka_consume, _kafka_publish),
    "rabbitmq": (_rabbit_consume, _rabbit_publish),
    "nats": (_nats_consume, _nats_publish),
    "redis": (_redis_consume, _redis_publish),
    "mqtt": (_mqtt_consume, _mqtt_publish),
}


class _DDTraceMiddleware:
    """FastStream :class:`BrokerMiddleware` factory.

    Instantiated once per broker; called once per message to produce a
    per-message :class:`_DDTraceMiddlewareInstance`.
    """

    __slots__ = ("_messaging_system",)

    def __init__(self, messaging_system: str) -> None:
        self._messaging_system = messaging_system

    def __call__(
        self,
        msg: Any,
        /,
        *,
        context: "ContextRepo",
    ) -> "_DDTraceMiddlewareInstance":
        return _DDTraceMiddlewareInstance(
            msg,
            context=context,
            messaging_system=self._messaging_system,
        )


class _DDTraceMiddlewareInstance(BaseMiddleware):
    def __init__(
        self,
        msg: Any,
        /,
        *,
        context: "ContextRepo",
        messaging_system: str,
    ) -> None:
        super().__init__(msg, context=context)
        self._messaging_system = messaging_system

    async def consume_scope(
        self,
        call_next: Callable[..., Awaitable[Any]],
        msg: "StreamMessage[Any]",
    ) -> Any:
        if config.faststream.distributed_tracing_enabled and getattr(msg, "headers", None):
            trace_utils.activate_distributed_headers(
                tracer,
                request_headers=msg.headers,
                int_config=config.faststream,
            )

        consume_fn, _ = _BROKER_HANDLERS.get(
            self._messaging_system, (_generic_consume, _generic_publish)
        )
        destination, extra_tags = consume_fn(msg)

        with tracer.trace(
            schematize_messaging_operation(
                "faststream.consume",
                provider=self._messaging_system,
                direction=SpanDirection.PROCESSING,
            ),
            span_type=SpanTypes.WORKER,
        ) as span:
            set_service_and_source(span, None, config.faststream)
            self._set_common_tags(span, SpanKind.CONSUMER, destination)
            self._set_message_tags(span, msg)
            for k, v in extra_tags.items():
                span._set_attribute(k, v)
            return await call_next(msg)

    async def publish_scope(
        self,
        call_next: Callable[["PublishCommand"], Awaitable[Any]],
        cmd: "PublishCommand",
    ) -> Any:
        _, publish_fn = _BROKER_HANDLERS.get(
            self._messaging_system, (_generic_consume, _generic_publish)
        )
        destination, extra_tags = publish_fn(cmd)

        with tracer.trace(
            schematize_messaging_operation(
                "faststream.publish",
                provider=self._messaging_system,
                direction=SpanDirection.OUTBOUND,
            ),
            span_type=SpanTypes.WORKER,
        ) as span:
            set_service_and_source(span, None, config.faststream)
            self._set_common_tags(span, SpanKind.PRODUCER, destination)
            correlation_id = getattr(cmd, "correlation_id", None)
            if correlation_id:
                span._set_attribute("messaging.message.conversation_id", correlation_id)
            for k, v in extra_tags.items():
                span._set_attribute(k, v)

            if config.faststream.distributed_tracing_enabled:
                # AIDEV-NOTE: cmd.headers is a dict in FastStream; mutating it
                # before call_next propagates to the underlying driver. If the
                # caller passed headers=None, replace it with an empty dict so
                # injection has somewhere to write.
                if getattr(cmd, "headers", None) is None:
                    cmd.headers = {}
                HTTPPropagator.inject(span.context, cmd.headers)

            return await call_next(cmd)

    def _set_common_tags(self, span: Any, span_kind: str, destination: Optional[str]) -> None:
        span._set_attribute(COMPONENT, config.faststream.integration_name)
        span._set_attribute(SPAN_KIND, span_kind)
        span._set_attribute(MESSAGING_SYSTEM, self._messaging_system)
        span._set_attribute(_SPAN_MEASURED_KEY, 1)
        if destination is not None:
            span.resource = destination
            span._set_attribute(MESSAGING_DESTINATION_NAME, destination)

    def _set_message_tags(self, span: Any, msg: "StreamMessage[Any]") -> None:
        message_id = getattr(msg, "message_id", None)
        if message_id:
            span._set_attribute(MESSAGING_MESSAGE_ID, message_id)
        correlation_id = getattr(msg, "correlation_id", None)
        if correlation_id:
            span._set_attribute("messaging.message.conversation_id", correlation_id)
        body = getattr(msg, "body", None)
        if body is not None:
            try:
                span._set_attribute("messaging.message.payload_size_bytes", len(body))
            except TypeError:
                pass
