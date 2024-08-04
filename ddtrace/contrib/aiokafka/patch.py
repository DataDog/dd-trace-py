import os

import aiokafka

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.pin import Pin


_AIOKafkaProducer = aiokafka.AIOKafkaProducer
_AIOKafkaConsumer = aiokafka.AIOKafkaConsumer

config._add(
    "kafka",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(os.getenv("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
        trace_empty_poll_enabled=asbool(os.getenv("DD_KAFKA_EMPTY_POLL_ENABLED", default=True)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(aiokafka, "__version__", "")


class TracedAIOKafkaProducerMixin:
    def __init__(self, config, *args, **kwargs):
        super(TracedAIOKafkaProducerMixin, self).__init__(config, *args, **kwargs)
        self._dd_bootstrap_servers = kwargs.get("bootstrap_servers")


class TracedAIOKafkaConsumerMixin:
    def __init__(self, config, *args, **kwargs):
        super(TracedAIOKafkaConsumerMixin, self).__init__(config, *args, **kwargs)
        self._group_id = kwargs.get("group.id", "")
        self._auto_commit = asbool(kwargs.get("enable_auto_commit", True))


class TracedAIOKafkaProducer(TracedAIOKafkaProducerMixin, aiokafka.AIOKafkaProducer):
    pass


class TracedAIOKafkaConsumer(TracedAIOKafkaConsumerMixin, aiokafka.AIOKafkaConsumer):
    pass


def patch():
    print("patching")
    if getattr(aiokafka, "_datadog_patch", False):
        return
    aiokafka._datadog_patch = True

    aiokafka.AIOKafkaProducer = TracedAIOKafkaProducer
    aiokafka.AIOKafkaConsumer = TracedAIOKafkaConsumer

    trace_utils.wrap(TracedAIOKafkaProducer, "send", traced_send)
    trace_utils.wrap(TracedAIOKafkaConsumer, "getone", traced_getone)
    trace_utils.wrap(TracedAIOKafkaConsumer, "getmany", traced_getmany)
    trace_utils.wrap(TracedAIOKafkaConsumer, "commit", traced_commit)

    Pin().onto(aiokafka.AIOKafkaProducer)
    Pin().onto(aiokafka.AIOKafkaConsumer)


def unpatch():
    print("unpatching")
    if getattr(aiokafka, "_datadog_patch", False):
        aiokafka._datadog_patch = False

    if trace_utils.iswrapped(TracedAIOKafkaProducer.send):
        trace_utils.unwrap(TracedAIOKafkaProducer, "send")
    if trace_utils.iswrapped(TracedAIOKafkaConsumer.getone):
        trace_utils.unwrap(TracedAIOKafkaConsumer, "getone")
    if trace_utils.iswrapped(TracedAIOKafkaConsumer.getmany):
        trace_utils.unwrap(TracedAIOKafkaConsumer, "getmany")
    if trace_utils.iswrapped(TracedAIOKafkaConsumer.commit):
        trace_utils.unwrap(TracedAIOKafkaConsumer, "commit")

    aiokafka.AIOKafkaProducer = _AIOKafkaProducer
    aiokafka.AIOKafkaConsumer = _AIOKafkaConsumer


async def traced_send():
    pass


async def traced_getone():
    pass


async def traced_getmany():
    pass


async def traced_commit():
    pass
