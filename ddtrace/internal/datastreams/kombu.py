from contextvars import ContextVar
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.contrib.internal.kombu.utils import HEADER_POS
from ddtrace.contrib.internal.kombu.utils import PUBLISH_BODY_IDX
from ddtrace.contrib.internal.kombu.utils import get_exchange_from_args
from ddtrace.contrib.internal.kombu.utils import get_routing_key_from_args
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_CELERY_EVENTS_EXCHANGE = "celeryev"
_CELERY_PIDBOX_SUFFIX = ".pidbox"

_MAX_PENDING_CELERY_PUBLISHES = 256

_pending_celery_publishes: ContextVar[tuple[str, ...]] = ContextVar("dsm_kombu_pending_celery_publishes", default=())


def is_celery_internal_exchange(exchange: Any) -> bool:
    return isinstance(exchange, str) and (
        exchange == _CELERY_EVENTS_EXCHANGE or exchange.endswith(_CELERY_PIDBOX_SUFFIX)
    )


def _pathway(headers: Any) -> Optional[str]:
    return headers.get(PROPAGATION_KEY_BASE_64) if isinstance(headers, dict) else None


def begin_celery_publish(headers: dict[str, Any]) -> None:
    pathway = _pathway(headers)
    if pathway is None:
        return
    pending = _pending_celery_publishes.get()
    _pending_celery_publishes.set((pending + (pathway,))[-_MAX_PENDING_CELERY_PUBLISHES:])


def pending_celery_publishes() -> tuple[str, ...]:
    return _pending_celery_publishes.get()


def restore_pending_celery_publishes(pending: tuple[str, ...]) -> None:
    _pending_celery_publishes.set(pending)


def _checkpointed_by_celery_publish(headers: Any) -> bool:
    pathway = _pathway(headers)
    return pathway is not None and pathway in _pending_celery_publishes.get()


def handle_kombu_produce(args, kwargs, span):
    from . import data_streams_processor as processor

    if _checkpointed_by_celery_publish(args[HEADER_POS]):
        return
    routing_key = get_routing_key_from_args(args)
    exchange = get_exchange_from_args(args)
    if is_celery_internal_exchange(exchange):
        return
    payload_size = 0
    payload_size += _calculate_byte_size(args[HEADER_POS])
    payload_size += _calculate_byte_size(args[PUBLISH_BODY_IDX])

    has_routing_key = str(bool(routing_key)).lower()

    # On the default (unnamed) exchange, the routing key is the destination queue name.
    if not exchange and routing_key:
        pathway_tags = ["direction:out", f"topic:{routing_key}", "type:rabbitmq"]
    else:
        pathway_tags = []
        for prefix, value in [
            ("direction", "out"),
            ("exchange", exchange),
            ("has_routing_key", has_routing_key),
            ("type", "rabbitmq"),
        ]:
            if value is not None:
                pathway_tags.append(f"{prefix}:{value}")

    if (p := processor()) is not None:
        ctx = p.set_checkpoint(pathway_tags, payload_size=payload_size, span=span)
        DsmPathwayCodec.encode(ctx, args[HEADER_POS])


def handle_kombu_consume(instance, message, span):
    from . import data_streams_processor as processor

    if is_celery_internal_exchange((message.delivery_info or {}).get("exchange")):
        return
    payload_size = 0
    payload_size += _calculate_byte_size(message.body)
    payload_size += _calculate_byte_size(message.headers)

    if (p := processor()) is not None:
        ctx = DsmPathwayCodec.decode(message.headers, p)
        queue = instance.queues[0].name if len(instance.queues) > 0 else ""
        ctx.set_checkpoint(["direction:in", f"topic:{queue}", "type:rabbitmq"], payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("kombu.amqp.publish.pre", handle_kombu_produce)
    core.on("kombu.amqp.receive.post", handle_kombu_consume)
