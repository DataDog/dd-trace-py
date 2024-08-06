import os

import aiokafka

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import kafka as kafkax
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.pin import Pin
from ddtrace.propagation.http import HTTPPropagator as Propagator


_AIOKafkaProducer = aiokafka.AIOKafkaProducer
_AIOKafkaConsumer = aiokafka.AIOKafkaConsumer

config._add(
    "kafka",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(os.getenv("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(aiokafka, "__version__", "")


class TracedAIOKafkaProducerMixin:
    def __init__(self, *args, **kwargs):
        super(TracedAIOKafkaProducerMixin, self).__init__(*args, **kwargs)
        self._dd_bootstrap_servers = kwargs.get("bootstrap_servers")


class TracedAIOKafkaConsumerMixin:
    def __init__(self, *args, **kwargs):
        super(TracedAIOKafkaConsumerMixin, self).__init__(*args, **kwargs)
        self._group_id = kwargs.get("group.id", "")
        self._auto_commit = asbool(kwargs.get("enable_auto_commit", True))


class TracedAIOKafkaProducer(TracedAIOKafkaProducerMixin, aiokafka.AIOKafkaProducer):
    pass


class TracedAIOKafkaConsumer(TracedAIOKafkaConsumerMixin, aiokafka.AIOKafkaConsumer):
    pass


def patch():
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


async def traced_send(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    topic = get_argument_value(args, kwargs, 0, "topic") or ""

    value = kwargs.get("value", None)
    message_key = kwargs.get("key", "") or ""
    partition = kwargs.get("partition", -1)
    headers = kwargs.get("headers", [])
    with pin.tracer.trace(
        schematize_messaging_operation(kafkax.PRODUCE, provider="kafka", direction=SpanDirection.OUTBOUND),
        service=trace_utils.ext_service(pin, config.kafka),
        span_type=SpanTypes.WORKER,
    ) as span:
        span.set_tag_str(MESSAGING_SYSTEM, kafkax.SERVICE)
        span.set_tag_str(COMPONENT, config.kafka.integration_name)
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        span.set_tag_str(kafkax.TOPIC, topic)
        span.set_tag_str(kafkax.MESSAGE_KEY, message_key)

        span.set_tag(kafkax.PARTITION, partition)
        span.set_tag_str(kafkax.TOMBSTONE, str(value is None))
        span.set_tag(SPAN_MEASURED_KEY)
        if instance._dd_bootstrap_servers is not None:
            span.set_tag_str(kafkax.HOST_LIST, ",".join(instance._dd_bootstrap_servers))
        rate = config.kafka.get_analytics_sample_rate()
        if rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)

        # inject headers with Datadog tags if trace propagation is enabled
        if config.kafka.distributed_tracing_enabled:
            # inject headers with Datadog tags:
            headers = get_argument_value(args, kwargs, 6, "headers", True) or {}
            Propagator.inject(span.context, headers)
            args, kwargs = set_argument_value(args, kwargs, 6, "headers", headers, override_unset=True)
        return func(*args, **kwargs)


async def traced_getone():
    pass


async def traced_getmany():
    pass


async def traced_commit(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)
    return func(*args, **kwargs)
