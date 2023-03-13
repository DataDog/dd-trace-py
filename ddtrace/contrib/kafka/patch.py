import confluent_kafka

from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor.wrapt import ObjectProxy

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...pin import Pin


_Producer = confluent_kafka.Producer
_Consumer = confluent_kafka.Consumer


class TracedProducer(ObjectProxy):
    def __init__(self, *args, **kwargs):
        producer = _Producer(*args, **kwargs)
        super(TracedProducer, self).__init__(producer)
        Pin(service="iamkafka").onto(self)

    def produce(self, topic, value=None, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            return self.__wrapped__.produce(topic, value=value, *args, **kwargs)

        with pin.tracer.trace(
            "kafkaproduce",
            service=trace_utils.ext_service(pin, config.kafka),
            span_type="kafkabar",
        ) as span:
            span.set_tag_str(COMPONENT, config.kafka.integration_name)
            span.set_tag_str(SPAN_KIND, "spankhind")
            span.set_tag_str("topic", "banana_topic")
            span.set_tag_str("bootstrap_servers", "numnah")
            span.set_tag(SPAN_MEASURED_KEY)
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.kafka.get_analytics_sample_rate())
        self.__wrapped__.produce(topic, value=value, *args, **kwargs)

    # in older versions of confluent_kafka, bool(Producer()) evaluates to False,
    # which makes the Pin functionality ignore it.
    def __bool__(self):
        return True

    __nonzero__ = __bool__


class TracedConsumer(ObjectProxy):
    def __init__(self, *args, **kwargs):
        consumer = _Consumer(*args, **kwargs)
        super(TracedConsumer, self).__init__(consumer)
        Pin(service="iamkafka").onto(self)

    def poll(self, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            return self.__wrapped__.poll(*args, **kwargs)

        with pin.tracer.trace(
            "kafkaconsume",
            service=trace_utils.ext_service(pin, config.kafka),
            span_type="kafkabar",
        ) as span:
            span.set_tag_str(COMPONENT, config.kafka.integration_name)
            span.set_tag_str(SPAN_KIND, "spankhind")
            span.set_tag_str("topic", "banana_topic")
            span.set_tag_str("bootstrap_servers", "numnah")
            span.set_tag_str("group_id", "Fhqwhgads")
            span.set_tag(SPAN_MEASURED_KEY)
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.kafka.get_analytics_sample_rate())
            return self.__wrapped__.poll(*args, **kwargs)


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
