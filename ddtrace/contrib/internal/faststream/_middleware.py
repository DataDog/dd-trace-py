"""Per-message middleware that the FastStream integration registers on every
broker. Implements FastStream's :class:`BrokerMiddleware` protocol.

Most of this file is mechanical attribute extraction per FastStream driver
(``raw_message`` exposes broker-specific fields). Dispatch is keyed on a
``driver`` string that distinguishes the underlying client library — important
because ``aiokafka.ConsumerRecord`` exposes ``topic``/``partition``/``offset``/
``key`` as plain attributes while ``confluent_kafka.Message`` exposes them as
zero-arg methods. The OTel-aligned ``messaging.system`` tag value is kept
separate so wire-level tags stay stable across drivers.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional

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


if TYPE_CHECKING:
    from faststream._internal.context.repository import ContextRepo
    from faststream.message import StreamMessage
    from faststream.response import PublishCommand


# AIDEV-NOTE: (module prefix, class name) → (driver, messaging.system).
# ``driver`` selects the per-broker tag extractor (aiokafka and
# confluent_kafka differ in raw_message shape); ``messaging.system`` is the
# OTel-aligned tag value sent on the span. We can't key on class name alone:
# ``faststream.kafka.KafkaBroker`` and ``faststream.confluent.KafkaBroker``
# share the same ``__name__``, so disambiguation requires the module prefix.
# MRO walk lets user subclasses inherit their parent broker's mapping.
_BROKER_TO_DRIVER: dict[tuple[str, str], tuple[str, str]] = {
    ("faststream.kafka", "KafkaBroker"): ("aiokafka", "kafka"),
    ("faststream.confluent", "KafkaBroker"): ("confluent_kafka", "kafka"),
    ("faststream.rabbit", "RabbitBroker"): ("rabbitmq", "rabbitmq"),
    ("faststream.nats", "NatsBroker"): ("nats", "nats"),
    ("faststream.redis", "RedisBroker"): ("redis", "redis"),
}

_KAFKA_DRIVERS = ("aiokafka", "confluent_kafka")


def detect_broker(broker: Any) -> tuple[str, str]:
    """Return ``(driver, messaging.system)`` for a FastStream broker."""
    for cls in type(broker).__mro__:
        module = getattr(cls, "__module__", "") or ""
        # e.g. "faststream.confluent.broker.broker" → "faststream.confluent"
        parts = module.split(".")
        if len(parts) >= 2 and parts[0] == "faststream":
            key = (f"{parts[0]}.{parts[1]}", cls.__name__)
            if key in _BROKER_TO_DRIVER:
                return _BROKER_TO_DRIVER[key]
    return "faststream", "faststream"


def detect_messaging_system(broker: Any) -> str:
    """Return the OTel ``messaging.system`` value for a FastStream broker."""
    return detect_broker(broker)[1]


# Each tag-extractor returns ``(destination, extra_tags_dict, optional list of
# per-record headers for span-links in batch consumes)``.
_TagsResult = tuple[Optional[str], dict[str, Any], Optional[list[dict[str, Any]]]]


def _msg_batch_headers(msg: "StreamMessage[Any]") -> Optional[list[dict[str, Any]]]:
    """Return ``msg.batch_headers`` when this is a batch consume, else None.

    FastStream normalizes per-record headers to ``list[dict[str, Any]]`` across
    all driver families (kafka, confluent, nats, redis); an empty list means
    "not a batch" and is treated as ``None`` here.
    """
    headers = getattr(msg, "batch_headers", None)
    return headers if headers else None


def _confluent_call(obj: Any, attr: str) -> Any:
    """Invoke a ``confluent_kafka.Message`` zero-arg accessor; ``None`` on failure."""
    fn = getattr(obj, attr, None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:
        return None


def _aiokafka_consume_tags(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    batch = _msg_batch_headers(msg)
    if batch is not None and isinstance(raw, (list, tuple)) and raw:
        return (
            getattr(raw[0], "topic", None),
            {"messaging.batch.message_count": len(raw)},
            batch,
        )
    tags: dict[str, Any] = {}
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


def _confluent_consume_tags(msg: "StreamMessage[Any]") -> _TagsResult:
    """Confluent-Kafka path: ``raw_message`` accessors are zero-arg methods."""
    raw = msg.raw_message
    batch = _msg_batch_headers(msg)
    if batch is not None and isinstance(raw, (list, tuple)) and raw:
        return (
            _confluent_call(raw[0], "topic"),
            {"messaging.batch.message_count": len(raw)},
            batch,
        )
    tags: dict[str, Any] = {}
    topic = _confluent_call(raw, "topic")
    partition = _confluent_call(raw, "partition")
    offset = _confluent_call(raw, "offset")
    key = _confluent_call(raw, "key")
    if partition is not None:
        tags["messaging.kafka.partition"] = partition
    if offset is not None:
        tags["messaging.kafka.message.offset"] = offset
    if key is not None:
        tags["messaging.kafka.message.key"] = key
    return topic, tags, None


def _kafka_publish_tags(cmd: "PublishCommand") -> _TagsResult:
    """Publish path is identical for aiokafka and confluent_kafka:
    ``PublishCommand`` is a FastStream object with plain attributes.
    """
    tags: dict[str, Any] = {}
    partition = getattr(cmd, "partition", None)
    key = getattr(cmd, "key", None)
    if partition is not None:
        tags["messaging.kafka.destination.partition"] = partition
    if key is not None:
        tags["messaging.kafka.message.key"] = key
    return getattr(cmd, "destination", None), tags, None


def _rabbit_consume_tags(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    exchange = getattr(raw, "exchange", "") or "default"
    routing_key = getattr(raw, "routing_key", "") or ""
    delivery_tag = getattr(raw, "delivery_tag", None)
    tags: dict[str, Any] = {
        "messaging.rabbitmq.destination.routing_key": routing_key,
    }
    if delivery_tag is not None:
        tags["messaging.rabbitmq.message.delivery_tag"] = delivery_tag
    destination = f"{exchange}.{routing_key}" if routing_key else exchange
    return destination, tags, None


def _rabbit_publish_tags(cmd: "PublishCommand") -> _TagsResult:
    exchange_obj = getattr(cmd, "exchange", None)
    exchange = getattr(exchange_obj, "name", None) or "default"
    routing_key = getattr(cmd, "destination", "") or ""
    tags: dict[str, Any] = {"messaging.rabbitmq.destination.routing_key": routing_key}
    destination = f"{exchange}.{routing_key}" if routing_key else exchange
    return destination, tags, None


def _nats_consume_tags(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    batch = _msg_batch_headers(msg)
    if batch is not None and isinstance(raw, (list, tuple)) and raw:
        return (
            getattr(raw[0], "subject", None),
            {"messaging.batch.message_count": len(raw)},
            batch,
        )
    return getattr(raw, "subject", None), {}, None


def _nats_publish_tags(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}, None


def _redis_consume_tags(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = msg.raw_message
    batch = _msg_batch_headers(msg)
    if isinstance(raw, dict):
        destination = raw.get("channel") or raw.get("list") or raw.get("stream")
        tags: dict[str, Any] = {}
        per_record_headers: Optional[list[dict[str, Any]]] = None
        if batch is not None:
            tags["messaging.batch.message_count"] = len(batch)
            per_record_headers = batch
        elif str(raw.get("type", "")).startswith("b") and isinstance(raw.get("data"), (list, tuple)):
            tags["messaging.batch.message_count"] = len(raw["data"])
        return destination, tags, per_record_headers
    return None, {}, None


def _redis_publish_tags(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}, None


def _generic_consume_tags(msg: "StreamMessage[Any]") -> _TagsResult:
    raw = getattr(msg, "raw_message", None)
    if raw is None:
        return None, {}, None
    for attr in ("topic", "subject", "channel", "exchange"):
        val = getattr(raw, attr, None)
        if val:
            return (val if isinstance(val, str) else str(val)), {}, None
    return None, {}, None


def _generic_publish_tags(cmd: "PublishCommand") -> _TagsResult:
    return getattr(cmd, "destination", None), {}, None


# Per-driver ``(consume_tags, publish_tags)`` pair.
_DRIVER_HANDLERS: dict[
    str,
    tuple[
        Callable[["StreamMessage[Any]"], _TagsResult],
        Callable[["PublishCommand"], _TagsResult],
    ],
] = {
    "aiokafka": (_aiokafka_consume_tags, _kafka_publish_tags),
    "confluent_kafka": (_confluent_consume_tags, _kafka_publish_tags),
    "rabbitmq": (_rabbit_consume_tags, _rabbit_publish_tags),
    "nats": (_nats_consume_tags, _nats_publish_tags),
    "redis": (_redis_consume_tags, _redis_publish_tags),
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


def _kafka_group_id_from_msg(msg: "StreamMessage[Any]") -> Optional[str]:
    """Pull the consumer group_id off a Kafka StreamMessage if available.

    FastStream attaches the source consumer config to ``msg._source_type`` /
    ``msg.consume_context`` depending on version; we walk a few likely
    attributes and fall back to ``None``.
    """
    for attr in ("group_id", "_group_id"):
        val = getattr(msg, attr, None)
        if val:
            return str(val)
    raw = getattr(msg, "raw_message", None)
    return getattr(raw, "group_id", None) if raw is not None else None


class _DDTraceMiddleware:
    """FastStream :class:`BrokerMiddleware` factory.

    Holds per-broker static state (driver, ``messaging.system`` value, and any
    Kafka bootstrap-servers we can pull off the broker once at construction
    time). Each call returns a fresh per-message middleware instance.
    """

    __slots__ = ("_driver", "_messaging_system", "_bootstrap_servers", "_group_id_resolver")

    def __init__(
        self,
        broker: Any = None,
        *,
        messaging_system: Optional[str] = None,
        driver: Optional[str] = None,
    ) -> None:
        if broker is not None:
            broker_driver, broker_system = detect_broker(broker)
            driver = driver or broker_driver
            messaging_system = messaging_system or broker_system
        if messaging_system is None:
            messaging_system = "faststream"
        if driver is None:
            # Legacy callers that only pass messaging_system. Default kafka
            # callers to aiokafka — the publish path is driver-agnostic, and
            # consume-path tests should pass a driver explicitly anyway.
            driver = "aiokafka" if messaging_system == "kafka" else messaging_system

        self._driver = driver
        self._messaging_system = messaging_system
        self._bootstrap_servers: Optional[str] = None
        # AIDEV-NOTE: group_id is per-subscriber, not per-broker, so we
        # capture a callable to resolve it from msg context at consume time.
        self._group_id_resolver: Callable[["StreamMessage[Any]"], Optional[str]] = lambda _msg: None
        if driver in _KAFKA_DRIVERS and broker is not None:
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
            driver=self._driver,
            messaging_system=self._messaging_system,
            bootstrap_servers=self._bootstrap_servers,
            group_id_resolver=self._group_id_resolver,
        )


class _DDTraceMiddlewareInstance(BaseMiddleware):
    # AIDEV-NOTE: The ``(msg, /, *, context, ...)`` shape mirrors FastStream's
    # ``BaseMiddleware.__init__`` (faststream/_internal/middlewares.py): the
    # base class enforces ``msg`` as positional-only and ``context`` as
    # keyword-only, and our ``super().__init__(msg, context=context)`` below
    # depends on that contract.
    def __init__(
        self,
        msg: Any,
        /,
        *,
        context: "ContextRepo",
        driver: str,
        messaging_system: str,
        bootstrap_servers: Optional[str] = None,
        group_id_resolver: Optional[Callable[["StreamMessage[Any]"], Optional[str]]] = None,
    ) -> None:
        super().__init__(msg, context=context)
        self._driver = driver
        self._messaging_system = messaging_system
        self._bootstrap_servers = bootstrap_servers
        self._group_id_resolver = group_id_resolver or (lambda _msg: None)

    async def consume_scope(
        self,
        call_next: Callable[..., Awaitable[Any]],
        msg: "StreamMessage[Any]",
    ) -> Any:
        consume_fn, _ = _DRIVER_HANDLERS.get(self._driver, (_generic_consume_tags, _generic_publish_tags))
        destination, extra_tags, batch_headers = consume_fn(msg)
        group_id = self._group_id_resolver(msg) if self._driver in _KAFKA_DRIVERS else None

        tags: dict[str, Any] = {
            COMPONENT: config.faststream.integration_name,
            SPAN_KIND: SpanKind.CONSUMER,
            MESSAGING_SYSTEM: self._messaging_system,
            _SPAN_MEASURED_KEY: 1,
        }
        if destination is not None:
            tags[MESSAGING_DESTINATION_NAME] = destination
        message_id = getattr(msg, "message_id", None)
        if message_id:
            tags[MESSAGING_MESSAGE_ID] = message_id
        correlation_id = getattr(msg, "correlation_id", None)
        if correlation_id:
            tags["messaging.message.conversation_id"] = correlation_id
        body = getattr(msg, "body", None)
        if body is not None:
            try:
                tags["messaging.message.payload_size_bytes"] = len(body)
            except TypeError:
                pass
        tags.update(extra_tags)
        if self._driver in _KAFKA_DRIVERS:
            tags[RECEIVED_MESSAGE] = "True"
            if self._bootstrap_servers:
                tags[HOST_LIST] = self._bootstrap_servers
            if group_id:
                tags[GROUP_ID] = group_id

        with core.context_with_data(
            "faststream.consume",
            span_name=schematize_messaging_operation(
                "faststream.consume",
                provider=self._messaging_system,
                direction=SpanDirection.PROCESSING,
            ),
            span_type=SpanTypes.WORKER,
            resource=destination,
            service=trace_utils.ext_service(None, config.faststream),
            tags=tags,
            integration_config=config.faststream,
            activate_distributed_headers=True,
            distributed_headers=getattr(msg, "headers", None),
        ) as ctx:
            # AIDEV-NOTE: For batched consumes, link each record's distributed
            # context as a span link so cross-record traces fan out correctly.
            if batch_headers and config.faststream.distributed_tracing_enabled:
                span = ctx.span
                for headers in batch_headers:
                    if not headers:
                        continue
                    try:
                        parent = HTTPPropagator.extract(headers)
                    except Exception:
                        continue
                    if parent is not None and parent.trace_id is not None:
                        span.link_span(parent)
            # DSM checkpoint — handler is registered in
            # ddtrace.internal.datastreams.faststream when DSM is enabled.
            # Destination is passed through so DSM doesn't re-extract it from
            # raw_message (which differs across drivers).
            core.dispatch(
                "faststream.consume.post",
                (msg, ctx.span, self._messaging_system, group_id, destination),
            )
            return await call_next(msg)

    async def publish_scope(
        self,
        call_next: Callable[["PublishCommand"], Awaitable[Any]],
        cmd: "PublishCommand",
    ) -> Any:
        _, publish_fn = _DRIVER_HANDLERS.get(self._driver, (_generic_consume_tags, _generic_publish_tags))
        destination, extra_tags, _ = publish_fn(cmd)

        tags: dict[str, Any] = {
            COMPONENT: config.faststream.integration_name,
            SPAN_KIND: SpanKind.PRODUCER,
            MESSAGING_SYSTEM: self._messaging_system,
            _SPAN_MEASURED_KEY: 1,
        }
        if destination is not None:
            tags[MESSAGING_DESTINATION_NAME] = destination
        correlation_id = getattr(cmd, "correlation_id", None)
        if correlation_id:
            tags["messaging.message.conversation_id"] = correlation_id
        tags.update(extra_tags)
        if self._driver in _KAFKA_DRIVERS:
            if self._bootstrap_servers:
                tags[HOST_LIST] = self._bootstrap_servers
            # Tombstone records have a None body, used in Kafka log compaction
            # to delete keys.
            body = getattr(cmd, "body", None)
            if body is None:
                body = getattr(cmd, "message", None)
            tags[TOMBSTONE] = str(body is None)

        with core.context_with_data(
            "faststream.publish",
            span_name=schematize_messaging_operation(
                "faststream.publish",
                provider=self._messaging_system,
                direction=SpanDirection.OUTBOUND,
            ),
            span_type=SpanTypes.WORKER,
            resource=destination,
            service=trace_utils.ext_service(None, config.faststream),
            tags=tags,
            integration_config=config.faststream,
        ) as ctx:
            if config.faststream.distributed_tracing_enabled:
                # AIDEV-NOTE: cmd.headers is a dict in FastStream; mutating it
                # before call_next propagates to the underlying driver. If the
                # caller passed headers=None, replace with an empty dict.
                if getattr(cmd, "headers", None) is None:
                    cmd.headers = {}
                HTTPPropagator.inject(ctx.span.context, cmd.headers)

            # DSM checkpoint — handler injects the DSM pathway header alongside
            # the trace-context header. Must run after HTTPPropagator.inject so
            # both headers land in cmd.headers.
            core.dispatch(
                "faststream.publish.pre",
                (cmd, ctx.span, self._messaging_system, destination),
            )

            return await call_next(cmd)
