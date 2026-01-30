import os
from time import time_ns
from typing import Dict

import aiokafka
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.events.messaging import MessagingCommitEvent
from ddtrace.contrib.events.messaging import MessagingConsumeBatchEvent
from ddtrace.contrib.events.messaging import MessagingConsumeEvent
from ddtrace.contrib.events.messaging import MessagingEvents
from ddtrace.contrib.events.messaging import MessagingProduceEvent
from ddtrace.contrib.events.messaging import MessagingProduceFinishedEvent
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator


config._add(
    "aiokafka",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(os.getenv("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
    ),
)


def get_version() -> str:
    return getattr(aiokafka, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"aiokafka": ">=0.9.0"}


def _parse_send_args(instance, args, kwargs):
    topic = get_argument_value(args, kwargs, 0, "topic")
    value = get_argument_value(args, kwargs, 1, "value", True)
    key = get_argument_value(args, kwargs, 2, "key", True) or None
    partition = get_argument_value(args, kwargs, 3, "partition", True)
    headers = get_argument_value(args, kwargs, 5, "headers", True) or []
    bootstrap_servers = instance.client._bootstrap_servers

    return topic, value, key, partition, headers, bootstrap_servers


def _extract_distributed_context(message):
    """Extract distributed tracing context from message headers."""
    if not message or not message.headers:
        return None

    dd_headers = {
        key: (val.decode("utf-8", errors="ignore") if isinstance(val, (bytes, bytearray)) else str(val))
        for key, val in message.headers
        if val is not None
    }
    return HTTPPropagator.extract(dd_headers)


async def traced_send(func, instance, args, kwargs):
    topic, value, key, partition, headers, bootstrap_servers = _parse_send_args(instance, args, kwargs)

    with core.context_with_event(
        MessagingProduceEvent(
            topic=topic,
            bootstrap_servers=bootstrap_servers,
            integration_config=config.aiokafka,
            value=value,
            key=key,
            partition=partition,
            headers=headers,
        )
    ) as ctx:
        args, kwargs = set_argument_value(args, kwargs, 5, "headers", headers, override_unset=True)

        try:
            result = await func(*args, **kwargs)
        except BaseException as e:
            core.dispatch_event(MessagingProduceFinishedEvent(ctx, (type(e), e, e.__traceback__), None))
            raise e

        def on_send_complete(future):
            try:
                result = future.result()
                core.dispatch_event(MessagingProduceFinishedEvent(ctx, (None, None, None), result))
            except Exception as e:
                core.dispatch_event(MessagingProduceFinishedEvent(ctx, (type(e), e, e.__traceback__), None))

        result.add_done_callback(on_send_complete)
        return result


async def traced_getone(func, instance, args, kwargs):
    """Traced wrapper for AIOKafkaConsumer.getone()"""
    start_ns = time_ns()
    message = None
    distributed_context = None

    group_id = instance._group_id
    bootstrap_servers = instance._client._bootstrap_servers

    try:
        message = await func(*args, **kwargs)
        if config.aiokafka.distributed_tracing_enabled:
            distributed_context = _extract_distributed_context(message)
    except Exception:
        raise
    finally:
        with core.context_with_event(
            MessagingConsumeEvent(
                topic=getattr(message, "topic", None) if message else None,
                bootstrap_servers=bootstrap_servers,
                group_id=group_id,
                integration_config=config.aiokafka,
                message=message,
                distributed_context=distributed_context,
                start_ns=start_ns,
            )
        ) as ctx:
            core.dispatch(
                MessagingEvents.CONSUME_START.value,
                (instance, ctx, start_ns, message, None),
            )

    return message


async def traced_getmany(func, instance, args, kwargs):
    """Traced wrapper for AIOKafkaConsumer.getmany()"""
    group_id = instance._group_id
    bootstrap_servers = instance._client._bootstrap_servers

    with core.context_with_event(
        MessagingConsumeBatchEvent(
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            integration_config=config.aiokafka,
        )
    ) as ctx:
        messages = await func(*args, **kwargs)
        ctx.set_item("messages", messages)

        core.dispatch(
            MessagingEvents.CONSUME_BATCH_START.value,
            (instance, ctx, messages),
        )

        return messages


async def traced_commit(func, instance, args, kwargs):
    result = await func(*args, **kwargs)

    core.dispatch_event(
        MessagingCommitEvent(
            group_id=instance.group_id, offsets=get_argument_value(args, kwargs, 1, "offsets", optional=True)
        )
    )

    return result


def patch():
    if getattr(aiokafka, "_datadog_patch", False):
        return
    aiokafka._datadog_patch = True

    _w("aiokafka", "AIOKafkaProducer.send", traced_send)
    _w("aiokafka", "AIOKafkaConsumer.getone", traced_getone)
    _w("aiokafka", "AIOKafkaConsumer.getmany", traced_getmany)
    _w("aiokafka.consumer.group_coordinator", "GroupCoordinator.commit_offsets", traced_commit)


def unpatch():
    if not getattr(aiokafka, "_datadog_patch", False):
        return

    aiokafka._datadog_patch = False

    _u(aiokafka.AIOKafkaProducer, "send")
    _u(aiokafka.AIOKafkaConsumer, "getone")
    _u(aiokafka.AIOKafkaConsumer, "getmany")
    _u(aiokafka.consumer.group_coordinator.GroupCoordinator, "commit_offsets")
