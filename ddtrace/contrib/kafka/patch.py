import time

import confluent_kafka
from confluent_kafka import TopicPartition

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import kafka as kafkax
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.pin import Pin


_Producer = confluent_kafka.Producer
_Consumer = confluent_kafka.Consumer
_SerializingProducer = confluent_kafka.SerializingProducer if hasattr(confluent_kafka, "SerializingProducer") else None
_DeserializingConsumer = (
    confluent_kafka.DeserializingConsumer if hasattr(confluent_kafka, "DeserializingConsumer") else None
)


config._add(
    "kafka",
    dict(
        _default_service=schematize_service_name("kafka"),
    ),
)


def get_version():
    # type: () -> str
    return getattr(confluent_kafka, "__version__", "")


class TracedProducer(confluent_kafka.Producer):
    def __init__(self, config, *args, **kwargs):
        super(TracedProducer, self).__init__(config, *args, **kwargs)
        self._dd_bootstrap_servers = (
            config.get("bootstrap.servers")
            if config.get("bootstrap.servers") is not None
            else config.get("metadata.broker.list")
        )

    def produce(self, topic, value=None, *args, **kwargs):
        super(TracedProducer, self).produce(topic, value, *args, **kwargs)

    # in older versions of confluent_kafka, bool(Producer()) evaluates to False,
    # which makes the Pin functionality ignore it.
    def __bool__(self):
        return True

    __nonzero__ = __bool__


class TracedConsumer(confluent_kafka.Consumer):
    def __init__(self, config, *args, **kwargs):
        super(TracedConsumer, self).__init__(config, *args, **kwargs)
        self._group_id = config.get("group.id", "")
        self._auto_commit = asbool(config.get("enable.auto.commit", True))

    def poll(self, timeout=None):
        return super(TracedConsumer, self).poll(timeout)

    def commit(self, message=None, *args, **kwargs):
        return super(TracedConsumer, self).commit(message, args, kwargs)


def patch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        return
    confluent_kafka._datadog_patch = True

    confluent_kafka.Producer = TracedProducer
    confluent_kafka.Consumer = TracedConsumer
    if _SerializingProducer is not None:
        confluent_kafka.SerializingProducer = TracedProducer
    if _DeserializingConsumer is not None:
        confluent_kafka.DeserializingConsumer = TracedConsumer

    trace_utils.wrap(TracedProducer, "produce", traced_produce)
    trace_utils.wrap(TracedConsumer, "poll", traced_poll)
    trace_utils.wrap(TracedConsumer, "commit", traced_commit)
    Pin().onto(confluent_kafka.Producer)
    Pin().onto(confluent_kafka.Consumer)


def unpatch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        confluent_kafka._datadog_patch = False

    if trace_utils.iswrapped(TracedProducer.produce):
        trace_utils.unwrap(TracedProducer, "produce")
    if trace_utils.iswrapped(TracedConsumer.poll):
        trace_utils.unwrap(TracedConsumer, "poll")
    if trace_utils.iswrapped(TracedConsumer.commit):
        trace_utils.unwrap(TracedConsumer, "commit")

    confluent_kafka.Producer = _Producer
    confluent_kafka.Consumer = _Consumer
    if _SerializingProducer is not None:
        confluent_kafka.SerializingProducer = _SerializingProducer
    if _DeserializingConsumer is not None:
        confluent_kafka.DeserializingConsumer = _DeserializingConsumer


def traced_produce(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    topic = get_argument_value(args, kwargs, 0, "topic") or ""
    core.set_item("kafka_topic", topic)
    try:
        value = get_argument_value(args, kwargs, 1, "value")
    except ArgumentError:
        value = None
    message_key = kwargs.get("key", "")
    partition = kwargs.get("partition", -1)
    if config._data_streams_enabled:
        # inject data streams context
        core.dispatch("kafka.produce.start", [instance, args, kwargs])

        on_delivery_kwarg = "on_delivery"
        on_delivery_arg = 5
        on_delivery = None
        try:
            on_delivery = get_argument_value(args, kwargs, on_delivery_arg, on_delivery_kwarg)
        except ArgumentError:
            on_delivery_kwarg = "callback"
            on_delivery_arg = 4
            try:
                on_delivery = get_argument_value(args, kwargs, on_delivery_arg, on_delivery_kwarg)
            except ArgumentError:
                on_delivery = None

        def wrapped_callback(err, msg):
            if err is None:
                if pin.tracer.data_streams_processor:
                    pin.tracer.data_streams_processor.track_kafka_produce(
                        msg.topic(), msg.partition(), msg.offset() or -1, time.time()
                    )
            if on_delivery is not None:
                on_delivery(err, msg)

        try:
            args, kwargs = set_argument_value(args, kwargs, on_delivery_arg, on_delivery_kwarg, wrapped_callback)
        except ArgumentError:
            # we set the callback even if it's not set by the client, to track produce calls correctly.
            kwargs[on_delivery_kwarg] = wrapped_callback

    with pin.tracer.trace(
        schematize_messaging_operation(kafkax.PRODUCE, provider="kafka", direction=SpanDirection.OUTBOUND),
        service=trace_utils.ext_service(pin, config.kafka),
        span_type=SpanTypes.WORKER,
    ) as span:
        span.set_tag_str(MESSAGING_SYSTEM, kafkax.SERVICE)
        span.set_tag_str(COMPONENT, config.kafka.integration_name)
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        span.set_tag_str(kafkax.TOPIC, topic)
        span.set_tag_str(kafkax.MESSAGE_KEY, ensure_text(message_key, errors="replace"))
        span.set_tag(kafkax.PARTITION, partition)
        span.set_tag_str(kafkax.TOMBSTONE, str(value is None))
        span.set_tag(SPAN_MEASURED_KEY)
        if instance._dd_bootstrap_servers is not None:
            span.set_tag_str(kafkax.HOST_LIST, instance._dd_bootstrap_servers)
        rate = config.kafka.get_analytics_sample_rate()
        if rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)
        return func(*args, **kwargs)


def traced_poll(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace(
        schematize_messaging_operation(kafkax.CONSUME, provider="kafka", direction=SpanDirection.PROCESSING),
        service=trace_utils.ext_service(pin, config.kafka),
        span_type=SpanTypes.WORKER,
    ) as span:
        message = func(*args, **kwargs)
        span.set_tag_str(MESSAGING_SYSTEM, kafkax.SERVICE)
        span.set_tag_str(COMPONENT, config.kafka.integration_name)
        span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
        span.set_tag_str(kafkax.RECEIVED_MESSAGE, str(message is not None))
        span.set_tag_str(kafkax.GROUP_ID, instance._group_id)
        if message is not None:
            if config._data_streams_enabled:
                core.set_item("kafka_topic", message.topic())
                core.dispatch("kafka.consume.start", [instance, message])
                if instance._auto_commit:
                    # it's not exactly true, but if auto commit is enabled, we consider that a message is acknowledged
                    # when it's read.
                    pin.tracer.data_streams_processor.track_kafka_commit(
                        instance._group_id, message.topic(), message.partition(), message.offset() or -1, time.time()
                    )

            message_key = message.key() or ""
            message_offset = message.offset() or -1
            span.set_tag_str(kafkax.TOPIC, message.topic())
            span.set_tag_str(kafkax.MESSAGE_KEY, ensure_text(message_key, errors="replace"))
            span.set_tag(kafkax.PARTITION, message.partition())
            span.set_tag_str(kafkax.TOMBSTONE, str(len(message) == 0))
            span.set_tag(kafkax.MESSAGE_OFFSET, message_offset)
        span.set_tag(SPAN_MEASURED_KEY)
        rate = config.kafka.get_analytics_sample_rate()
        if rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)
        return message


def traced_commit(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    if config._data_streams_enabled:
        message = get_argument_value(args, kwargs, 0, "message")
        offsets = kwargs.get("offsets", [])
        if message is not None:
            offsets = [TopicPartition(message.topic(), message.partition(), offset=message.offset())]
        for offset in offsets:
            pin.tracer.data_streams_processor.track_kafka_commit(
                instance._group_id, offset.topic, offset.partition, offset.offset or -1, time.time()
            )
    return func(*args, **kwargs)
