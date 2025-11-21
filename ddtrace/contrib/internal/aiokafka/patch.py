import os
from time import time_ns
from typing import Dict

import aiokafka
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext.kafka import CONSUME
from ddtrace.ext.kafka import GROUP_ID
from ddtrace.ext.kafka import HOST_LIST
from ddtrace.ext.kafka import PRODUCE
from ddtrace.ext.kafka import SERVICE
from ddtrace.ext.kafka import TOPIC
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
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


def common_aiokafka_tags(topic, bootstrap_servers):
    return {
        COMPONENT: config.aiokafka.integration_name,
        TOPIC: topic,
        MESSAGING_DESTINATION_NAME: topic,
        MESSAGING_SYSTEM: SERVICE,
        HOST_LIST: bootstrap_servers,
    }


def common_consume_aiokafka_tags(topic, bootstrap_servers, group_id):
    tags = common_aiokafka_tags(topic, bootstrap_servers)
    tags.update(
        {
            SPAN_KIND: SpanKind.CONSUMER,
            GROUP_ID: group_id,
        }
    )
    return tags


def parse_send(instance, args, kwargs):
    topic = get_argument_value(args, kwargs, 0, "topic")
    value = get_argument_value(args, kwargs, 1, "value", True)
    key = get_argument_value(args, kwargs, 2, "key", True) or None
    partition = get_argument_value(args, kwargs, 3, "partition", True)
    headers = get_argument_value(args, kwargs, 5, "headers", True) or []
    servers = instance.client._bootstrap_servers

    return topic, value, headers, partition, key, servers


async def traced_send(func, instance, args, kwargs):
    topic, value, headers, partition, key, bootstrap_servers = parse_send(instance, args, kwargs)

    with core.context_with_data(
        "aiokafka.send",
        span_name=schematize_messaging_operation(PRODUCE, provider="kafka", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.aiokafka),
        tags=common_aiokafka_tags(topic, bootstrap_servers),
    ) as ctx:
        core.dispatch("aiokafka.send.start", (topic, value, key, headers, ctx, partition))
        args, kwargs = set_argument_value(args, kwargs, 5, "headers", headers, override_unset=True)

        try:
            result = await func(*args, **kwargs)
        except BaseException as e:
            core.dispatch("aiokafka.send.completed", (ctx, (type(e), e, e.__traceback__), None))
            raise e

        def sent_callback(future):
            try:
                result = future.result()
                core.dispatch("aiokafka.send.completed", (ctx, (None, None, None), result))
            except Exception as e:
                core.dispatch("aiokafka.send.completed", (ctx, (type(e), e, e.__traceback__), None))

        result.add_done_callback(sent_callback)
        return result


async def traced_getone(func, instance, args, kwargs):
    # we must get start time now since execute before starting a span in order to get distributed context
    # if it exists
    start_ns = time_ns()
    err = None
    message = None
    parent_ctx = None

    group_id = instance._group_id
    bootstrap_servers = instance._client._bootstrap_servers

    try:
        message = await func(*args, **kwargs)
        if config.aiokafka.distributed_tracing_enabled and message.headers:
            dd_headers = {
                key: (val.decode("utf-8", errors="ignore") if isinstance(val, (bytes, bytearray)) else str(val))
                for key, val in message.headers
                if val is not None
            }
            parent_ctx = HTTPPropagator.extract(dd_headers)
    except Exception as e:
        err = e
        raise err
    finally:
        with core.context_with_data(
            "aiokafka.getone",
            call_trace=False,
            span_name=schematize_messaging_operation(CONSUME, provider="kafka", direction=SpanDirection.INBOUND),
            span_type=SpanTypes.WORKER,
            service=trace_utils.ext_service(None, config.aiokafka),
            distributed_context=parent_ctx,
            tags=common_consume_aiokafka_tags(getattr(message, "topic", None), bootstrap_servers, group_id),
        ) as ctx:
            core.dispatch("aiokafka.getone.message", (instance, ctx, start_ns, message, err))
    return message


async def traced_getmany(func, instance, args, kwargs):
    group_id = instance._group_id
    bootstrap_servers = instance._client._bootstrap_servers

    with core.context_with_data(
        "aiokafka.getmany",
        call_trace=False,
        span_name=schematize_messaging_operation(CONSUME, provider="kafka", direction=SpanDirection.INBOUND),
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.aiokafka),
        tags=common_consume_aiokafka_tags(None, bootstrap_servers, group_id),
    ) as ctx:
        messages = await func(*args, **kwargs)

        core.dispatch("aiokafka.getmany.message", (instance, ctx, messages))

        return messages


async def traced_commit(func, instance, args, kwargs):
    result = await func(*args, **kwargs)
    core.dispatch("aiokafka.commit.end", (instance, args, kwargs))
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
