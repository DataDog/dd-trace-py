import os

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
from ddtrace.ext.kafka import MESSAGE_KEY
from ddtrace.ext.kafka import PARTITION
from ddtrace.ext.kafka import PRODUCE
from ddtrace.ext.kafka import SERVICE
from ddtrace.ext.kafka import TOMBSTONE
from ddtrace.ext.kafka import TOPIC
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.pin import Pin


config._add(
    "aiokafka",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(os.getenv("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
    ),
)


def get_version() -> str:
    return getattr(aiokafka, "__version__", "")


def patch():
    if getattr(aiokafka, "_datadog_patch", False):
        return
    aiokafka._datadog_patch = True

    _w("aiokafka", "AIOKafkaProducer.send_and_wait", traced_send_and_wait)
    _w("aiokafka", "AIOKafkaConsumer.getone", traced_getone)

    Pin().onto(aiokafka.AIOKafkaProducer)
    Pin().onto(aiokafka.AIOKafkaConsumer)


def unpatch():
    if not getattr(aiokafka, "_datadog_patch", False):
        return

    aiokafka._datadog_patch = False

    _u(aiokafka.AIOKafkaProducer, "send_and_wait")
    _u(aiokafka.AIOKafkaConsumer, "getone")


def bootstrap_servers(instance):
    if hasattr(instance, "client"):
        client = instance.client
    if hasattr(instance, "_client"):
        client = instance._client
    if client._bootstrap_servers is not None:
        return ",".join(client._bootstrap_servers)


def create_send_span_tags(instance, args, kwargs):
    topic = get_argument_value(args, kwargs, 0, "topic")
    tags = {
        COMPONENT: config.aiokafka.integration_name,
        SPAN_KIND: SpanKind.PRODUCER,
        TOPIC: topic,
        HOST_LIST: bootstrap_servers(instance),
        MESSAGING_SYSTEM: SERVICE,
    }
    partition = get_argument_value(args, kwargs, 3, "partition", True) or None
    if partition:
        tags[PARTITION] = partition
    key = get_argument_value(args, kwargs, 2, "key", True) or None
    if key:
        tags[MESSAGE_KEY] = key.decode("utf-8")
    value = get_argument_value(args, kwargs, 1, "value", True) or None
    tags[TOMBSTONE] = str(value is None).lower()
    return tags


async def traced_send_and_wait(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with core.context_with_data(
        "aiokafka.send_and_wait",
        parent=None,
        span_name=schematize_messaging_operation(PRODUCE, provider="kafka", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.WORKER,
        call_key="instrumented_send_and_wait",
        service=trace_utils.ext_service(pin, config.aiokafka),
        tags=create_send_span_tags(instance, args, kwargs),
        pin=pin,
    ) as ctx, ctx[ctx["call_key"]]:
        result = await func(*args, **kwargs)
        return result


def create_get_span_tags(instance, args, kwargs):
    tags = {
        COMPONENT: config.aiokafka.integration_name,
        SPAN_KIND: SpanKind.CONSUMER,
        HOST_LIST: bootstrap_servers(instance),
        MESSAGING_SYSTEM: SERVICE,
        GROUP_ID: instance._group_id,
    }
    return tags


async def traced_getone(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with core.context_with_data(
        "aiokafka.getone",
        parent=None,
        span_name=schematize_messaging_operation(CONSUME, provider="kafka", direction=SpanDirection.INBOUND),
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(pin, config.aiokafka),
        call_key="instrumented_getone",
        tags=create_get_span_tags(instance, args, kwargs),
        pin=pin,
    ) as ctx, ctx[ctx["call_key"]]:
        err = None
        result = None
        try:
            result = await func(*args, **kwargs)
        except Exception as e:
            err = e
            raise err
        finally:
            core.dispatch("aiokafka.getone.message", (ctx, result, err))
        return result
