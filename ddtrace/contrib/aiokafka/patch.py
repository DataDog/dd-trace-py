import os
import sys

import aiokafka

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import kafka as kafkax
from ddtrace.internal import core
from ddtrace.internal.compat import time_ns
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
        return await func(*args, **kwargs)

    topic = get_argument_value(args, kwargs, 0, "topic")
    value = get_argument_value(args, kwargs, 1, "value", True) or None
    key = get_argument_value(args, kwargs, 2, "key", True) or ""
    partition = get_argument_value(args, kwargs, 3, "partition", True) or None
    headers = get_argument_value(args, kwargs, 5, "headers", True) or []

    with pin.tracer.trace(
        schematize_messaging_operation(kafkax.PRODUCE, provider="kafka", direction=SpanDirection.OUTBOUND),
        service=trace_utils.ext_service(pin, config.kafka),
        span_type=SpanTypes.WORKER,
    ) as span:
        core.dispatch("aiokafka.produce.start", (instance, topic, value, key, headers, span))
        span.set_tag_str(MESSAGING_SYSTEM, kafkax.SERVICE)
        span.set_tag_str(COMPONENT, config.kafka.integration_name)
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        span.set_tag_str(kafkax.TOPIC, topic)
        span.set_tag_str(kafkax.MESSAGE_KEY, key)

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
            additional_headers = {}
            Propagator.inject(span.context, additional_headers)
            for header, value in additional_headers.items():
                headers.append((header, value.encode("utf-8")))

        args, kwargs = set_argument_value(args, kwargs, 5, "headers", headers, override_unset=True)
        return await func(*args, **kwargs)


async def traced_getone(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    # we must get start time now since execute before starting a span in order to get distributed context
    # if it exists
    start_ns = time_ns()
    err = None
    result = None
    try:
        result = await func(*args, **kwargs)
    except Exception as e:
        err = e
        raise err
    finally:
        _instrument_message(result, pin, start_ns, instance, err)
    return result


async def traced_getmany(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    # we must get start time now since execute before starting a span in order to get distributed context
    # if it exists
    start_ns = time_ns()
    err = None
    result = None
    try:
        result = await func(*args, **kwargs)
    except Exception as e:
        err = e
        raise err
    finally:
        for tp, records in result.items():
            for record in records:
                _instrument_message(record, pin, start_ns, instance, err)
    return result


def _instrument_message(message, pin, start_ns, instance, err):
    ctx = None
    if message is not None and config.kafka.distributed_tracing_enabled and message.headers:
        ctx = Propagator.extract(dict(message.headers))
    with pin.tracer.start_span(
        name=schematize_messaging_operation(kafkax.CONSUME, provider="kafka", direction=SpanDirection.PROCESSING),
        service=trace_utils.ext_service(pin, config.kafka),
        span_type=SpanTypes.WORKER,
        child_of=ctx if ctx is not None else pin.tracer.context_provider.active(),
        activate=True,
    ) as span:
        # reset span start time to before function call
        span.start_ns = start_ns

        span.set_tag_str(MESSAGING_SYSTEM, kafkax.SERVICE)
        span.set_tag_str(COMPONENT, config.kafka.integration_name)
        span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
        span.set_tag_str(kafkax.GROUP_ID, instance._group_id)
        if message is not None:
            message_key = message.key or ""
            message_offset = message.offset or -1
            span.set_tag_str(kafkax.TOPIC, message.topic)

            core.set_item("kafka_topic", message.topic)
            core.dispatch("aiokafka.consume.start", (instance, message, span))

            # If this is a deserializing consumer, do not set the key as a tag since we
            # do not have the serialization function
            if isinstance(message_key, str) or isinstance(message_key, bytes):
                span.set_tag_str(kafkax.MESSAGE_KEY, message_key)
            span.set_tag(kafkax.PARTITION, message.partition)
            is_tombstone = False
            try:
                is_tombstone = len(message) == 0
            except TypeError:  # https://github.com/confluentinc/confluent-kafka-python/issues/1192
                pass
            span.set_tag_str(kafkax.TOMBSTONE, str(is_tombstone))
            span.set_tag(kafkax.MESSAGE_OFFSET, message_offset)
        span.set_tag(SPAN_MEASURED_KEY)
        rate = config.kafka.get_analytics_sample_rate()
        if rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)

        if err is not None:
            span.set_exc_info(*sys.exc_info())


async def traced_commit(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)
    core.dispatch("aiokafka.commit.start", (instance, args, kwargs))
    return await func(*args, **kwargs)
