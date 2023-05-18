import confluent_kafka

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import kafka as kafkax
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.pin import Pin


_Producer = confluent_kafka.Producer
_Consumer = confluent_kafka.Consumer


config._add(
    "kafka",
    dict(_default_service="kafka"),
)


class TracedProducer(confluent_kafka.Producer):
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
        self._group_id = config["group.id"]

    def poll(self, timeout=1):
        return super(TracedConsumer, self).poll(timeout)


def patch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        return
    setattr(confluent_kafka, "_datadog_patch", True)

    confluent_kafka.Producer = TracedProducer
    confluent_kafka.Consumer = TracedConsumer

    trace_utils.wrap(TracedProducer, "produce", traced_produce)
    trace_utils.wrap(TracedConsumer, "poll", traced_poll)
    Pin().onto(confluent_kafka.Producer)
    Pin().onto(confluent_kafka.Consumer)


def unpatch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        setattr(confluent_kafka, "_datadog_patch", False)

    if trace_utils.iswrapped(TracedProducer.produce):
        trace_utils.unwrap(TracedProducer, "produce")
    if trace_utils.iswrapped(TracedConsumer.poll):
        trace_utils.unwrap(TracedConsumer, "poll")

    confluent_kafka.Producer = _Producer
    confluent_kafka.Consumer = _Consumer


def traced_produce(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    topic = get_argument_value(args, kwargs, 0, "topic")
    try:
        value = get_argument_value(args, kwargs, 1, "value")
    except ArgumentError:
        value = None
    message_key = kwargs.get("key", "")
    partition = kwargs.get("partition", -1)

    with pin.tracer.trace(
        kafkax.PRODUCE,
        service=trace_utils.ext_service(pin, config.kafka),
        span_type=SpanTypes.WORKER,
    ) as span:
        span.set_tag_str(MESSAGING_SYSTEM, kafkax.SERVICE)
        span.set_tag_str(COMPONENT, config.kafka.integration_name)
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        span.set_tag_str(kafkax.TOPIC, topic)
        span.set_tag_str(kafkax.MESSAGE_KEY, ensure_text(message_key))
        span.set_tag(kafkax.PARTITION, partition)
        span.set_tag_str(kafkax.TOMBSTONE, str(value is None))
        span.set_tag(SPAN_MEASURED_KEY)
        rate = config.kafka.get_analytics_sample_rate()
        if rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)
        return func(*args, **kwargs)


def traced_poll(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace(
        kafkax.CONSUME,
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
            message_key = message.key() or ""
            message_offset = message.offset() or -1
            span.set_tag_str(kafkax.TOPIC, message.topic())
            span.set_tag_str(kafkax.MESSAGE_KEY, ensure_text(message_key))
            span.set_tag(kafkax.PARTITION, message.partition())
            span.set_tag_str(kafkax.TOMBSTONE, str(len(message) == 0))
            span.set_tag(kafkax.MESSAGE_OFFSET, message_offset)
        span.set_tag(SPAN_MEASURED_KEY)
        rate = config.kafka.get_analytics_sample_rate()
        if rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)
        return message
