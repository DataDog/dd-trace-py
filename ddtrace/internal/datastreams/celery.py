from __future__ import annotations

from contextvars import ContextVar
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.kombu import begin_celery_publish
from ddtrace.internal.datastreams.kombu import is_celery_internal_exchange
from ddtrace.internal.datastreams.kombu import pending_celery_publishes
from ddtrace.internal.datastreams.kombu import restore_pending_celery_publishes
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size


_sends_in_flight: ContextVar[tuple[tuple[Any, tuple[str, ...]], ...]] = ContextVar(
    "dsm_celery_sends_in_flight", default=()
)

_BROKER_TYPES = {
    "amqp": "rabbitmq",
    "amqps": "rabbitmq",
    "pyamqp": "rabbitmq",
    "librabbitmq": "rabbitmq",
    "redis": "redis",
    "rediss": "redis",
    "sentinel": "redis",
}


def _broker_type(app: Any, side_url_setting: str) -> Optional[str]:
    conf = app.conf
    url = getattr(conf, side_url_setting, None) or conf.broker_url
    if isinstance(url, (list, tuple)):
        url = url[0] if url else None
    scheme: Any = str(url).split("://", 1)[0] if url else conf.get("broker_transport") or "amqp"
    if not isinstance(scheme, str):
        return None
    return _BROKER_TYPES.get(scheme.split("+", 1)[0].lower())


def _edge_tags(direction: str, exchange: Optional[str], routing_key: Optional[str], broker_type: str) -> list[str]:
    if routing_key:
        return [f"direction:{direction}", f"topic:{routing_key}", f"type:{broker_type}"]
    tags = [f"direction:{direction}"]
    if exchange:
        tags.append(f"exchange:{exchange}")
    tags.append(f"type:{broker_type}")
    return tags


def _exchange_name(exchange: Any) -> Optional[str]:
    name = getattr(exchange, "name", exchange)
    return name if isinstance(name, str) else None


def _routing_key(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _payload_size(*values: Any) -> int:
    total = 0
    seen: set[int] = set()
    stack = list(values)
    while stack:
        value = stack.pop()
        if isinstance(value, (str, bytes)):
            total += _calculate_byte_size(value)
        elif isinstance(value, (dict, list, tuple)):
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, dict):
                for key, item in value.items():
                    stack.append(key)
                    stack.append(item)
            else:
                stack.extend(value)
        elif isinstance(value, (int, float)):
            total += len(str(value))
    return total


def dsm_celery_task_publish(app: Any, signal_kwargs: dict[str, Any], span: Optional[Any]) -> None:
    from . import data_streams_processor as processor

    headers = signal_kwargs.get("headers")
    if not isinstance(headers, dict):
        return
    exchange = _exchange_name(signal_kwargs.get("exchange"))
    if is_celery_internal_exchange(exchange):
        return
    broker_type = _broker_type(_publishing_app(app), "broker_write_url")
    if broker_type is None:
        return
    p = processor()
    if p is None:
        return

    tags = _edge_tags("out", exchange, _routing_key(signal_kwargs.get("routing_key")), broker_type)
    payload_size = _payload_size(signal_kwargs.get("body"), headers)
    ctx = p.set_checkpoint(tags, payload_size=payload_size, span=span)
    DsmPathwayCodec.encode(ctx, headers)
    begin_celery_publish(headers)


def dsm_celery_send_task_start(app: Any) -> None:
    _sends_in_flight.set(_sends_in_flight.get() + ((app, pending_celery_publishes()),))


def dsm_celery_send_task_end() -> None:
    sends = _sends_in_flight.get()
    if not sends:
        return
    _sends_in_flight.set(sends[:-1])
    restore_pending_celery_publishes(sends[-1][1])


def _publishing_app(signal_app: Any) -> Any:
    sends = _sends_in_flight.get()
    return sends[-1][0] if sends else signal_app


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
    message_headers = carrier.get("headers")
    if not isinstance(message_headers, dict):
        message_headers = {}
    tags = _edge_tags("in", exchange, _routing_key(delivery_info.get("routing_key")), broker_type)
    payload_size = _payload_size(message_headers, carrier.get("args"), carrier.get("kwargs"))
    ctx = DsmPathwayCodec.decode({**message_headers, **carrier}, p)
    ctx.set_checkpoint(tags, payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("celery.task.publish.pre", dsm_celery_task_publish)
    core.on("celery.send_task.start", dsm_celery_send_task_start)
    core.on("celery.send_task.end", dsm_celery_send_task_end)
    core.on("celery.task.prerun", dsm_celery_task_prerun)
