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
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import ObjectProxy


_Producer = confluent_kafka.Producer
_Consumer = confluent_kafka.Consumer


config._add(
    "kafka",
    dict(_default_service="kafka"),
)


class TracedProducer(ObjectProxy):
    def __init__(self, *args, **kwargs):
        producer = _Producer(*args, **kwargs)
        super(TracedProducer, self).__init__(producer)
        Pin().onto(self)

    def produce(self, *args, **kwargs):
        func = self.__wrapped__.produce
        topic = get_argument_value(args, kwargs, 0, "topic")
        try:
            value = get_argument_value(args, kwargs, 1, "value")
        except ArgumentError:
            value = None
        message_key = kwargs.get("key", "")
        partition = kwargs.get("partition", -1)

        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with pin.tracer.trace(
            kafkax.PRODUCE,
            service=trace_utils.ext_service(pin, config.kafka),
            span_type=SpanTypes.WORKER,
        ) as span:
            span.set_tag_str("messaging.system", kafkax.SERVICE)
            span.set_tag_str(COMPONENT, config.kafka.integration_name)
            span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
            span.set_tag_str(kafkax.TOPIC, topic)
            span.set_tag_str(kafkax.MESSAGE_KEY, ensure_text(message_key))
            span.set_tag(kafkax.PARTITION, partition)
            span.set_tag_str(kafkax.TOMBSTONE, str(value is None))
            span.set_tag(SPAN_MEASURED_KEY)
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.kafka.get_analytics_sample_rate())
            return func(*args, **kwargs)

    # in older versions of confluent_kafka, bool(Producer()) evaluates to False,
    # which makes the Pin functionality ignore it.
    def __bool__(self):
        return True

    __nonzero__ = __bool__


class TracedConsumer(ObjectProxy):
    def __init__(self, *args, **kwargs):
        consumer = _Consumer(*args, **kwargs)
        super(TracedConsumer, self).__init__(consumer)
        Pin().onto(self)

    def poll(self, *args, **kwargs):
        func = self.__wrapped__.poll
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with pin.tracer.trace(
            kafkax.CONSUME,
            service=trace_utils.ext_service(pin, config.kafka),
            span_type=SpanTypes.WORKER,
        ) as span:
            message = func(*args, **kwargs)
            span.set_tag_str("messaging.system", kafkax.SERVICE)
            span.set_tag_str(COMPONENT, config.kafka.integration_name)
            span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
            span.set_tag_str(kafkax.RECEIVED_MESSAGE, str(message is not None))
            if message is not None:
                message_key = message.key() or ""
                span.set_tag_str(kafkax.TOPIC, message.topic())
                span.set_tag_str(kafkax.MESSAGE_KEY, ensure_text(message_key))
                span.set_tag(kafkax.PARTITION, message.partition())
                span.set_tag_str(kafkax.TOMBSTONE, str(len(message) == 0))
            span.set_tag(SPAN_MEASURED_KEY)
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.kafka.get_analytics_sample_rate())
            return message


def patch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        return
    setattr(confluent_kafka, "_datadog_patch", True)

    setattr(confluent_kafka, "Producer", TracedProducer)
    setattr(confluent_kafka, "Consumer", TracedConsumer)


def unpatch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        setattr(confluent_kafka, "_datadog_patch", False)

    setattr(confluent_kafka, "Producer", _Producer)
    setattr(confluent_kafka, "Consumer", _Consumer)
