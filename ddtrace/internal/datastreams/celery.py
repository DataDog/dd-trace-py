from __future__ import annotations

from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.kombu import is_celery_internal_exchange
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size


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
    url_text = str(url) if url else ""
    scheme: Any = url_text.split("://", 1)[0] if "://" in url_text else conf.get("broker_transport") or "amqp"
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


def _arguments_size(args: Any, kwargs: Any) -> int:
    size = _calculate_byte_size(kwargs)
    if isinstance(args, (list, tuple)):
        size += sum(_calculate_byte_size(arg) for arg in args)
    return size


def _body_size(body: Any) -> int:
    if isinstance(body, tuple) and len(body) >= 2:
        return _arguments_size(body[0], body[1])
    if isinstance(body, dict):
        return _arguments_size(body.get("args"), body.get("kwargs"))
    return _calculate_byte_size(body)


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

    tags = _edge_tags("out", exchange, _routing_key(signal_kwargs.get("routing_key")), broker_type)
    payload_size = _body_size(signal_kwargs.get("body")) + _calculate_byte_size(headers)
    ctx = p.set_checkpoint(tags, payload_size=payload_size, span=span)
    DsmPathwayCodec.encode(ctx, headers)


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
    payload_size = _calculate_byte_size(message_headers) + _arguments_size(carrier.get("args"), carrier.get("kwargs"))
    ctx = DsmPathwayCodec.decode({**message_headers, **carrier}, p)
    ctx.set_checkpoint(tags, payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("celery.task.publish.pre", dsm_celery_task_publish)
    core.on("celery.task.prerun", dsm_celery_task_prerun)
