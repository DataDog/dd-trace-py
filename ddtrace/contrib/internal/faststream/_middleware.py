"""Per-message middleware that the FastStream integration registers on every
broker. Implements FastStream's :class:`BrokerMiddleware` protocol.

Most of this file is mechanical attribute extraction per broker driver
(``raw_message`` exposes broker-specific fields). Dispatch is keyed on the
detected ``messaging.system`` value via a simple dict — no class hierarchy.
"""
from typing import TYPE_CHECKING
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from faststream._internal.middlewares import BaseMiddleware

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext.kafka import GROUP_ID
from ddtrace.ext.kafka import HOST_LIST
from ddtrace.ext.kafka import RECEIVED_MESSAGE
from ddtrace.ext.kafka import TOMBSTONE
from ddtrace.internal import core
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


# AIDEV-NOTE: messaging.system value is OTel-aligned. MRO walk lets broker
# subclasses inherit their parent's mapping.
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


# Each handler returns (destination, extra_tags_dict, optional list of dicts of
# per-record headers for span_link in batch consumes).
_TagsResult = Tuple[Optional[str], Dict[str, Any], Optional[List[Dict[str, Any]]]]


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
    return topic, tags, None


def _kafka_publish(cmd: "PublishCommand") -> _TagsResult:
    tags: Dict[str, Any] = {}
    partition = getattr(cmd, "partition", None)
    key = getattr(cmd, "key", None)
    if partition is not None:
        tags["messaging.kafka.destination.partition"] = partition
    if key is not None:
        tags["messaging.kafka.message.key"] = key
    return getattr(cmd, "destination", None), tags, None


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
    destination = f"{exchange}.{routing_key}" if routing_key else exchange
    return destination, tags, None


def _rabbit_publish(cmd: "PublishCommand") -> _TagsResult:
    exchange_obj = getattr(cmd, "exchange", None)
    exchange = getattr(exchange_obj, "name", None) or "default"
    routing_key = getattr(cmd, "destination", "") or ""
    tags: Dict[str, Any] = {"messaging.rabbitmq.destination.routing_key": routing_key}
    destination = f"{exchange}.{routing_key}" if routing_key else exchange
    return destination, tags, None


def _nats_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    if isinstance(raw, (list, tuple)) and raw:
        first = raw[0]
        per_record_headers = [getattr(m, "headers", {}) or {} for m in raw]
        return (
            getattr(first, "subject", None),
            {"messaging.batch.message_count": len(raw)},
            per_record_headers,
        )
    return getattr(raw, "subject", None), {}, None


def _nats_publish(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}, None


def _redis_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    if isinstance(raw, dict):
        destination = raw.get("channel") or raw.get("list") or raw.get("stream")
        tags: Dict[str, Any] = {}
        per_record_headers: Optional[List[Dict[str, Any]]] = None
        if str(raw.get("type", "")).startswith("b") and isinstance(raw.get("data"), (list, tuple)):
            tags["messaging.batch.message_count"] = len(raw["data"])
            # For Redis batches, per-record headers aren't natively exposed —
            # callers will still get the span; just no extra links.
            per_record_headers = None
        return destination, tags, per_record_headers
    return None, {}, None


def _redis_publish(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}, None


def _mqtt_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    return getattr(msg.raw_message, "topic", None), {}, None


def _mqtt_publish(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}, None


def _generic_consume(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = getattr(msg, "raw_message", None)
    if raw is None:
        return None, {}, None
    for attr in ("topic", "subject", "channel", "exchange"):
        val = getattr(raw, attr, None)
        if val:
            return (val if isinstance(val, str) else str(val)), {}, None
    return None, {}, None


def _generic_publish(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}, None


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


def _kafka_bootstrap_servers(broker: Any) -> Optional[str]:
    """Best-effort retrieval of the Kafka bootstrap-servers list from the broker.

    FastStream stores it under ``broker._connection_kwargs['bootstrap_servers']``
    after construction. Returned as a comma-separated string to match aiokafka.
    """
    kwargs = getattr(broker, "_connection_kwargs", None) or {}
    servers = kwargs.get("bootstrap_servers")
    if servers is None:
        return None
    if isinstance(servers, str):
        return servers
    if isinstance(servers, (list, tuple)):
        return ",".join(str(s) for s in servers)
    return str(servers)


class _DDTraceMiddleware:
    """FastStream :class:`BrokerMiddleware` factory.

    Holds per-broker static state (the ``messaging.system`` value plus any
    Kafka bootstrap-servers / group_id we can pull off the broker once at
    construction time). Each call returns a fresh per-message middleware.
    """

    __slots__ = ("_messaging_system", "_bootstrap_servers", "_group_id_resolver")

    def __init__(
        self,
        messaging_system: str,
        broker: Any = None,
    ) -> None:
        self._messaging_system = messaging_system
        self._bootstrap_servers: Optional[str] = None
        # AIDEV-NOTE: group_id is per-subscriber, not per-broker, so we
        # capture a callable to resolve it from msg context at consume time.
        self._group_id_resolver: Callable[["StreamMessage[Any]"], Optional[str]] = (
            lambda _msg: None
        )
        if messaging_system == "kafka" and broker is not None:
            self._bootstrap_servers = _kafka_bootstrap_servers(broker)
            self._group_id_resolver = _kafka_group_id_from_msg

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
            bootstrap_servers=self._bootstrap_servers,
            group_id_resolver=self._group_id_resolver,
        )


def _kafka_group_id_from_msg(msg: "StreamMessage[Any]") -> Optional[str]:
    """Pull the consumer group_id off a Kafka StreamMessage if available.

    FastStream attaches the source consumer config to ``msg._source_type`` /
    ``msg.consume_context`` depending on version; we walk a few likely
    attributes and fall back to None.
    """
    for attr in ("group_id", "_group_id"):
        val = getattr(msg, attr, None)
        if val:
            return str(val)
    raw = getattr(msg, "raw_message", None)
    return getattr(raw, "group_id", None) if raw is not None else None


class _DDTraceMiddlewareInstance(BaseMiddleware):
    def __init__(
        self,
        msg: Any,
        /,
        *,
        context: "ContextRepo",
        messaging_system: str,
        bootstrap_servers: Optional[str] = None,
        group_id_resolver: Optional[Callable[["StreamMessage[Any]"], Optional[str]]] = None,
    ) -> None:
        super().__init__(msg, context=context)
        self._messaging_system = messaging_system
        self._bootstrap_servers = bootstrap_servers
        self._group_id_resolver = group_id_resolver or (lambda _msg: None)

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
        destination, extra_tags, batch_headers = consume_fn(msg)

        with tracer.trace(
            schematize_messaging_operation(
                "faststream.consume",
                provider=self._messaging_system,
                direction=SpanDirection.PROCESSING,
            ),
            span_type=SpanTypes.WORKER,
        ) as span:
            span.service = trace_utils.ext_service(None, config.faststream)
            self._set_common_tags(span, SpanKind.CONSUMER, destination)
            self._set_message_tags(span, msg)
            for k, v in extra_tags.items():
                span._set_attribute(k, v)
            if self._messaging_system == "kafka":
                self._set_kafka_consume_tags(span, msg)
                span._set_attribute(RECEIVED_MESSAGE, "True")
            # AIDEV-NOTE: For batched consumes, link each record's distributed
            # context as a span link so cross-record traces fan out correctly.
            if batch_headers and config.faststream.distributed_tracing_enabled:
                for headers in batch_headers:
                    if not headers:
                        continue
                    try:
                        ctx = HTTPPropagator.extract(headers)
                    except Exception:
                        continue
                    if ctx is not None and ctx.trace_id is not None:
                        span.link_span(ctx)
            # DSM checkpoint — handler is registered in
            # ddtrace.internal.datastreams.faststream when DSM is enabled.
            group_id = self._group_id_resolver(msg) if self._messaging_system == "kafka" else None
            core.dispatch(
                "faststream.consume.post", (msg, span, self._messaging_system, group_id)
            )
            return await call_next(msg)

    async def publish_scope(
        self,
        call_next: Callable[["PublishCommand"], Awaitable[Any]],
        cmd: "PublishCommand",
    ) -> Any:
        _, publish_fn = _BROKER_HANDLERS.get(
            self._messaging_system, (_generic_consume, _generic_publish)
        )
        destination, extra_tags, _ = publish_fn(cmd)

        with tracer.trace(
            schematize_messaging_operation(
                "faststream.publish",
                provider=self._messaging_system,
                direction=SpanDirection.OUTBOUND,
            ),
            span_type=SpanTypes.WORKER,
        ) as span:
            span.service = trace_utils.ext_service(None, config.faststream)
            self._set_common_tags(span, SpanKind.PRODUCER, destination)
            correlation_id = getattr(cmd, "correlation_id", None)
            if correlation_id:
                span._set_attribute("messaging.message.conversation_id", correlation_id)
            for k, v in extra_tags.items():
                span._set_attribute(k, v)
            if self._messaging_system == "kafka":
                if self._bootstrap_servers:
                    span._set_attribute(HOST_LIST, self._bootstrap_servers)
                # Tombstone records have a None body, used in Kafka log
                # compaction to delete keys.
                body = getattr(cmd, "body", None)
                if body is None:
                    body = getattr(cmd, "message", None)
                span._set_attribute(TOMBSTONE, str(body is None))

            if config.faststream.distributed_tracing_enabled:
                # AIDEV-NOTE: cmd.headers is a dict in FastStream; mutating it
                # before call_next propagates to the underlying driver. If the
                # caller passed headers=None, replace with an empty dict.
                if getattr(cmd, "headers", None) is None:
                    cmd.headers = {}
                HTTPPropagator.inject(span.context, cmd.headers)

            # DSM checkpoint — handler injects the DSM pathway header alongside
            # the trace-context header. Must run after HTTPPropagator.inject so
            # both headers land in cmd.headers.
            core.dispatch("faststream.publish.pre", (cmd, span, self._messaging_system))

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

    def _set_kafka_consume_tags(self, span: Any, msg: "StreamMessage[Any]") -> None:
        if self._bootstrap_servers:
            span._set_attribute(HOST_LIST, self._bootstrap_servers)
        group_id = self._group_id_resolver(msg)
        if group_id:
            span._set_attribute(GROUP_ID, group_id)
