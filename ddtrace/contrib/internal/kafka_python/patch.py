from time import time_ns
from typing import Any
from typing import Callable
from typing import Optional

import kafka
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.kafka import KafkaConsumeEvent
from ddtrace.contrib._events.kafka import KafkaProducerEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.ext.kafka import CONSUME
from ddtrace.ext.kafka import PRODUCE
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings import env
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


log = get_logger(__name__)


config._add(
    "kafka_python",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(env.get("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
    ),
)


def get_version() -> str:
    return getattr(kafka, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"kafka": ">=2.0.0"}


def _cluster_id(cluster: Any) -> str:
    return getattr(cluster, "cluster_id", None) or ""


def _producer_cluster(instance: Any) -> Any:
    return getattr(instance, "_metadata", None)


def _consumer_cluster(instance: Any) -> Any:
    return getattr(getattr(instance, "_client", None), "cluster", None)


def _bootstrap_servers(instance: Any) -> Optional[str]:
    servers = getattr(instance, "config", {}).get("bootstrap_servers")
    if isinstance(servers, (list, tuple)):
        return ",".join(map(str, servers))
    return servers


def _decode_key(key: Any) -> Any:
    if isinstance(key, (bytes, bytearray)):
        return key.decode("utf-8", errors="replace")
    return key


def _headers_supported(instance: Any) -> bool:
    api_version = getattr(instance, "config", {}).get("api_version")
    return api_version is None or tuple(api_version) >= (0, 11)


def traced_send(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if not is_tracing_enabled():
        return func(*args, **kwargs)

    topic = get_argument_value(args, kwargs, 0, "topic")
    value = get_argument_value(args, kwargs, 1, "value", optional=True)
    key = get_argument_value(args, kwargs, 2, "key", optional=True)
    headers = get_argument_value(args, kwargs, 3, "headers", optional=True)
    partition = get_argument_value(args, kwargs, 4, "partition", optional=True)
    cluster_id = _cluster_id(_producer_cluster(instance))
    inject = _headers_supported(instance) and (headers is None or isinstance(headers, list))
    tracing_headers: dict[str, str] = {}

    event = KafkaProducerEvent(
        messaging_operation=PRODUCE,
        provider="kafka",
        topic=topic,
        bootstrap_servers=_bootstrap_servers(instance),
        distributed_headers=tracing_headers,
        component=config.kafka_python.integration_name,
        integration_config=config.kafka_python,
        service=trace_utils.ext_service(None, config.kafka_python),
        cluster_id=cluster_id,
        tombstone=value is None,
        message_key=_decode_key(key) if key is not None else "None",
        partition=partition,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        core.set_item("kafka_cluster_id", cluster_id)
        if inject:
            new_headers = list(headers or [])
            new_headers.extend((k, v.encode("utf-8")) for k, v in tracing_headers.items())
            core.dispatch("kafka_python.send.start", (topic, value, key, new_headers, ctx))
            args, kwargs = set_argument_value(args, kwargs, 3, "headers", new_headers, override_unset=True)

        try:
            future = func(*args, **kwargs)
        except BaseException as error:
            ctx.dispatch_ended_event(type(error), error, error.__traceback__)
            raise

        def on_success(record_metadata: Any) -> None:
            result_partition = getattr(record_metadata, "partition", None)
            result_offset = getattr(record_metadata, "offset", None)
            if isinstance(result_partition, int):
                event.partition = result_partition
            if isinstance(result_offset, int):
                event.message_offset = result_offset
            core.dispatch("kafka_python.send.completed", (record_metadata, cluster_id))
            ctx.dispatch_ended_event()

        def on_error(error: BaseException) -> None:
            ctx.dispatch_ended_event(type(error), error, error.__traceback__)

        try:
            future.add_callback(on_success)
            future.add_errback(on_error)
        except Exception:
            log.debug("Unable to observe kafka-python send result", exc_info=True)
            ctx.dispatch_ended_event()
        return future


def _consume_event(instance: Any, topic: Optional[str], **kwargs: Any) -> KafkaConsumeEvent:
    return KafkaConsumeEvent(
        messaging_operation=CONSUME,
        provider="kafka",
        direction=SpanDirection.INBOUND,
        topic=topic,
        bootstrap_servers=_bootstrap_servers(instance),
        group_id=getattr(instance, "config", {}).get("group_id"),
        use_active_context=False,
        activate=False,
        component=config.kafka_python.integration_name,
        integration_config=config.kafka_python,
        service=trace_utils.ext_service(None, config.kafka_python),
        **kwargs,
    )


def traced_next(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if not is_tracing_enabled():
        return func(*args, **kwargs)

    start_ns = time_ns()
    record = func(*args, **kwargs)

    event = _consume_event(
        instance,
        record.topic,
        message_headers=[dict(record.headers)] if record.headers else [],
    )
    with core.context_with_event(event) as ctx:
        span_from_context(ctx).start_ns = start_ns
        cluster_id = _cluster_id(_consumer_cluster(instance))
        core.set_item("kafka_cluster_id", cluster_id)
        event.cluster_id = cluster_id
        event.received_message = True
        event.tombstone = record.value is None
        message_key = _decode_key(record.key)
        if isinstance(message_key, str):
            event.message_key = message_key
        event.partition = record.partition
        event.message_offset = record.offset
        core.dispatch("kafka_python.consume.message", (instance, ctx, record))
    return record


def traced_poll(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if not is_tracing_enabled() or get_argument_value(args, kwargs, 2, "update_offsets", optional=True) is False:
        return func(*args, **kwargs)

    start_ns = time_ns()
    err: Optional[BaseException] = None
    records: Any = None
    try:
        records = func(*args, **kwargs)
    except Exception as e:
        err = e

    topics_partitions: dict[str, list[int]] = {}
    message_headers: list[dict[str, Any]] = []
    for tp, batch in (records or {}).items():
        topics_partitions.setdefault(tp.topic, []).append(tp.partition)
        message_headers.extend(dict(record.headers) for record in batch if record.headers)

    event = _consume_event(
        instance,
        next(iter(topics_partitions), None),
        propagation_as_span_links=True,
        message_headers=message_headers,
        error=err,
    )
    with core.context_with_event(event) as ctx:
        span_from_context(ctx).start_ns = start_ns
        cluster_id = _cluster_id(_consumer_cluster(instance))
        core.set_item("kafka_cluster_id", cluster_id)
        event.cluster_id = cluster_id
        event.received_message = bool(records)
        if topics_partitions:
            event.topics_partitions = topics_partitions
        for batch in (records or {}).values():
            for record in batch:
                core.dispatch("kafka_python.consume.message", (instance, ctx, record))

    if err is not None:
        raise err
    return records


def traced_commit_offsets(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    result = func(*args, **kwargs)
    offsets = get_argument_value(args, kwargs, 0, "offsets", optional=True)
    if offsets:
        cluster_id = _cluster_id(_consumer_cluster(instance))
        core.dispatch("kafka_python.commit", (instance.group_id, offsets, cluster_id))
    return result


def patch() -> None:
    if getattr(kafka, "_datadog_patch", False):
        return
    kafka._datadog_patch = True

    _w("kafka.producer.kafka", "KafkaProducer.send", traced_send)
    _w("kafka.consumer.group", "KafkaConsumer.__next__", traced_next)
    _w("kafka.consumer.group", "KafkaConsumer.poll", traced_poll)
    _w("kafka.coordinator.consumer", "ConsumerCoordinator.commit_offsets_sync", traced_commit_offsets)
    _w("kafka.coordinator.consumer", "ConsumerCoordinator.commit_offsets_async", traced_commit_offsets)


def unpatch() -> None:
    if not getattr(kafka, "_datadog_patch", False):
        return
    kafka._datadog_patch = False

    from kafka.consumer.group import KafkaConsumer
    from kafka.coordinator.consumer import ConsumerCoordinator
    from kafka.producer.kafka import KafkaProducer

    _u(KafkaProducer, "send")
    _u(KafkaConsumer, "__next__")
    _u(KafkaConsumer, "poll")
    _u(ConsumerCoordinator, "commit_offsets_sync")
    _u(ConsumerCoordinator, "commit_offsets_async")
