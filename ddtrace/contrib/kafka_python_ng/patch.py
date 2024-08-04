import os
import sys

import kafka

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import kafka as kafkax
from ddtrace.internal.compat import time_ns
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.pin import Pin
from ddtrace.propagation.http import HTTPPropagator as Propagator


_KafkaProducer = kafka.KafkaProducer
_KafkaConsumer = kafka.KafkaConsumer

config._add(
    "kafka",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(os.getenv("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
        trace_empty_poll_enabled=asbool(os.getenv("DD_KAFKA_EMPTY_POLL_ENABLED", default=True)),
    ),
)


def get_version():
    return getattr(kafka, "__version__", "")


class TracedKafkaProducerMixin:
    def __init__(self, **config):
        super(TracedKafkaProducerMixin, self).__init__(**config)
        self._dd_bootstrap_servers = (
            config.get("bootstrap_servers")
            if config.get("bootstrap_servers") is not None
            else config.get("metadata_broker_list")
        )


class TracedKafkaConsumerMixin:
    def __init__(self, *topics, **configs):
        super(TracedKafkaConsumerMixin, self).__init__(*topics, **configs)
        self._group_id = configs.get("group_id", "")
        self._auto_commit = asbool(configs.get("enable_auto_commit", True))


class TracedKafkaConsumer(TracedKafkaConsumerMixin, kafka.KafkaConsumer):
    pass


class TracedKafkaProducer(TracedKafkaProducerMixin, kafka.KafkaProducer):
    pass


def patch():
    if getattr(kafka, "_datadog_patch", False):
        return

    kafka.KafkaProducer = TracedKafkaProducer
    kafka.KafkaConsumer = TracedKafkaConsumer

    trace_utils.wrap(TracedKafkaProducer, "send", traced_send)
    trace_utils.wrap(TracedKafkaConsumer, "poll", traced_poll)
    trace_utils.wrap(TracedKafkaConsumer, "commit", traced_commit)

    Pin().onto(kafka.KafkaProducer)
    Pin().onto(kafka.KafkaConsumer)

    kafka._datadog_patch = True


def unpatch():
    if trace_utils.iswrapped(TracedKafkaProducer.send):
        trace_utils.unwrap(TracedKafkaProducer, "send")
    if trace_utils.iswrapped(TracedKafkaConsumer.poll):
        trace_utils.unwrap(TracedKafkaConsumer, "poll")
    if trace_utils.iswrapped(TracedKafkaConsumer.commit):
        trace_utils.unwrap(TracedKafkaConsumer, "commit")

    kafka.KafkaProducer = _KafkaProducer
    kafka.KafkaConsumer = _KafkaConsumer

    if getattr(kafka, "_datadog_patch", False):
        kafka._datadog_patch = False


def traced_send(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    topic = get_argument_value(args, kwargs, 0, "topic") or ""
    try:
        value = get_argument_value(args, kwargs, 1, "value")
    except ArgumentError:
        value = None
    message_key = kwargs.get("key", "") or ""
    partition = kwargs.get("partition", -1)
    headers = get_argument_value(args, kwargs, 6, "headers", optional=True) or {}
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
            headers = kwargs.get("headers", [])
            additional_headers = {}
            Propagator.inject(span.context, additional_headers)
            for header, value in additional_headers.items():
                headers.append((header, value.encode("utf-8")))
            kwargs["headers"] = headers
        return func(*args, **kwargs)


def traced_poll(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # we must get start time now since execute before starting a span in order to get distributed context
    # if it exists
    start_ns = time_ns()
    err = None
    result = None
    try:
        result = func(*args, **kwargs)
    except Exception as e:
        err = e
        raise err
    finally:
        if isinstance(result, dict):
            if (config.kafka.trace_empty_poll_enabled and len(result) == 0) or len(result) >= 1:
                _instrument_message(result, pin, start_ns, instance, err)
    return result


def traced_commit(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    return func(*args, **kwargs)


def _instrument_message(messages, pin, start_ns, instance, err):
    ctx = None
    # First message is used to extract context and enrich datadog spans
    # This approach aligns with the opentelemetry confluent kafka semantics
    first_message = None
    try:
        first_message = next(iter(messages.values()))[0]
    except StopIteration:
        pass
    if first_message is not None and config.kafka.distributed_tracing_enabled and first_message.headers:
        ctx = Propagator.extract(dict(first_message.headers))
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
        span.set_tag_str(kafkax.RECEIVED_MESSAGE, str(first_message is not None))
        span.set_tag_str(kafkax.GROUP_ID, instance._group_id)
        if first_message is not None:
            message_key = first_message.key or ""
            span.set_tag_str(kafkax.MESSAGE_KEY, message_key)

            message_offset = first_message.offset or -1
            span.set_tag(kafkax.MESSAGE_OFFSET, message_offset)

            span.set_tag_str(kafkax.TOPIC, first_message.topic)
            span.set_tag(kafkax.PARTITION, first_message.partition)
            is_tombstone = len(first_message) == 0
            span.set_tag_str(kafkax.TOMBSTONE, str(is_tombstone))

        span.set_tag(SPAN_MEASURED_KEY)
        rate = config.kafka.get_analytics_sample_rate()
        if rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)

        if err is not None:
            span.set_exc_info(*sys.exc_info())
