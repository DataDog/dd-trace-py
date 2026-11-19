from __future__ import annotations

from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.kombu import is_celery_internal_exchange
from ddtrace.internal.datastreams.kombu import skip_kombu_produce_checkpoint
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_BROKER_TYPES = {
    "amqp": "rabbitmq",
    "amqps": "rabbitmq",
    "pyamqp": "rabbitmq",
    "librabbitmq": "rabbitmq",
    "redis": "redis",
    "rediss": "redis",
}


def _broker_type(app: Any, side_url_setting: str) -> Optional[str]:
    conf = app.conf
    url = conf.get(side_url_setting) or conf.get("broker_url")
    if isinstance(url, (list, tuple)):
        url = url[0] if url else None
    scheme: Any = str(url).split("://", 1)[0] if url else conf.get("broker_transport") or "amqp"
    if not isinstance(scheme, str):
        return None
    return _BROKER_TYPES.get(scheme.split("+", 1)[0].lower())


def _queue_name(app: Any, exchange: Optional[str], routing_key: Optional[str]) -> Optional[str]:
    if not exchange:
        return routing_key or None
    try:
        queues = app.amqp.queues.values()
    except Exception:
        log.debug("unable to read the celery app queues", exc_info=True)
        return None
    for queue in queues:
        queue_exchange = getattr(queue, "exchange", None)
        if getattr(queue_exchange, "name", None) == exchange and queue.routing_key == routing_key:
            return str(queue.name)
    return None


def _edge_tags(
    direction: str, app: Any, exchange: Optional[str], routing_key: Optional[str], broker_type: str
) -> list[str]:
    queue = _queue_name(app, exchange, routing_key)
    if queue:
        return [f"direction:{direction}", f"topic:{queue}", f"type:{broker_type}"]
    tags = [f"direction:{direction}"]
    if exchange:
        tags.append(f"exchange:{exchange}")
    tags.append(f"has_routing_key:{str(bool(routing_key)).lower()}")
    tags.append(f"type:{broker_type}")
    return tags


def _exchange_name(exchange: Any) -> Optional[str]:
    name = getattr(exchange, "name", exchange)
    return name if isinstance(name, str) else None


def dsm_celery_task_publish(app: Any, signal_kwargs: dict[str, Any], span: Optional[Any]) -> None:
    from . import data_streams_processor as processor

    headers = signal_kwargs.get("headers")
    if not isinstance(headers, dict):
        return
    exchange = _exchange_name(signal_kwargs.get("exchange"))
    if is_celery_internal_exchange(exchange):
        return
    broker_type = _broker_type(app, "broker_write_url")
    if broker_type is None:
        return
    p = processor()
    if p is None:
        return

    tags = _edge_tags("out", app, exchange, signal_kwargs.get("routing_key"), broker_type)
    payload_size = _calculate_byte_size(signal_kwargs.get("body")) + _calculate_byte_size(headers)
    ctx = p.set_checkpoint(tags, payload_size=payload_size, span=span)
    DsmPathwayCodec.encode(ctx, headers)
    skip_kombu_produce_checkpoint(headers)


def dsm_celery_task_prerun(task: Any, span: Optional[Any]) -> None:
    from . import data_streams_processor as processor

    request = task.request
    if getattr(request, "is_eager", False):
        return
    delivery_info = getattr(request, "delivery_info", None) or {}
    exchange = _exchange_name(delivery_info.get("exchange"))
    broker_type = _broker_type(task.app, "broker_read_url")
    if broker_type is None:
        return
    p = processor()
    if p is None:
        return

    carrier = vars(request)
    tags = _edge_tags("in", task.app, exchange, delivery_info.get("routing_key"), broker_type)
    payload_size = _calculate_byte_size(carrier.get("headers")) + _calculate_byte_size(carrier.get("kwargs"))
    ctx = DsmPathwayCodec.decode(carrier, p)
    ctx.set_checkpoint(tags, payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("celery.task.publish.pre", dsm_celery_task_publish)
    core.on("celery.task.prerun", dsm_celery_task_prerun)
