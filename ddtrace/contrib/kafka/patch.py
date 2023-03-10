import confluent_kafka

from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor import wrapt

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...pin import Pin


_original_kafka_producer = None
_original_kafka_consumer = None


class TracedProducer(confluent_kafka.Producer):
    def produce(
        self, topic, value=None, *args, **kwargs
    ):
        super(TracedProducer, self).produce(topic, value, *args, **kwargs)

    # in older versions of confluent_kafka, bool(Producer()) evaluates to False,
    # which makes the Pin functionality ignore it.
    def __bool__(self):
        return True

    __nonzero__ = __bool__


class TracedConsumer(confluent_kafka.Consumer):
    def __init__(self, config):
        super(TracedConsumer, self).__init__(config)

    def poll(self, timeout=-1):
        return super(TracedConsumer, self).poll(timeout)


def patch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        return
    setattr(confluent_kafka, "_datadog_patch", True)

    global _original_kafka_consumer
    global _original_kafka_producer
    _original_kafka_producer = confluent_kafka.Producer
    _original_kafka_consumer = confluent_kafka.Consumer

    confluent_kafka.Producer = TracedProducer
    confluent_kafka.Consumer = TracedConsumer

    def _inner_wrap_produce(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)
        return wrap_produce(
            func, instance, pin, config.kafka, args, kwargs
        )

    def _inner_wrap_poll(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)
        return wrap_poll(
            func, instance, pin, config.kafka, args, kwargs
        )

    wrapt.wrap_function_wrapper(
        TracedProducer,
        "produce",
        _inner_wrap_produce,
    )

    wrapt.wrap_function_wrapper(
        TracedConsumer,
        "poll",
        _inner_wrap_poll,
    )
    Pin(service="iamkafka").onto(confluent_kafka.Producer)
    Pin(service="iamkafka").onto(confluent_kafka.Consumer)


def unpatch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        setattr(confluent_kafka, "_datadog_patch", False)

    trace_utils.unwrap(TracedProducer, "produce")
    trace_utils.unwrap(TracedConsumer, "poll")

    confluent_kafka.Producer = _original_kafka_producer
    confluent_kafka.Consumer = _original_kafka_consumer


def wrap_produce(func, instance, pin, integration_config, args, kwargs):
    with pin.tracer.trace(
        "kafkaproduce", service=trace_utils.ext_service(pin, integration_config), span_type="kafkabar"
    ) as span:
        span.set_tag_str(COMPONENT, integration_config.integration_name)
        span.set_tag_str(SPAN_KIND, "spankhind")
        span.set_tag_str("topic", "banana_topic")
        span.set_tag_str("bootstrap_servers", "numnah")
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, integration_config.get_analytics_sample_rate())
        return func(*args, **kwargs)


def wrap_poll(func, instance, pin, integration_config, args, kwargs):
    with pin.tracer.trace(
        "kafkaconsume", service=trace_utils.ext_service(pin, integration_config), span_type="kafkabar"
    ) as span:
        span.set_tag_str(COMPONENT, integration_config.integration_name)
        span.set_tag_str(SPAN_KIND, "spankhind")
        span.set_tag_str("topic", "banana_topic")
        span.set_tag_str("bootstrap_servers", "numnah")
        span.set_tag_str("group_id", "Fhqwhgads")
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, integration_config.get_analytics_sample_rate())
        return func(*args, **kwargs)
