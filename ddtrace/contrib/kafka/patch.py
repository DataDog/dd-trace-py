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
        super().produce(topic, value, *args, **kwargs)


class TracedConsumer(confluent_kafka.Consumer):
    def __init__(self, config):
        super().__init__(config)

    def poll(self, timeout=-1):
        return super().poll(timeout)



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

    #wrapt.wrap_function_wrapper(
    #    TracedConsumer,
    #    "poll",
    #    _inner_wrap_poll,
    #)
    Pin(service=None).onto(confluent_kafka.Producer)
    Pin(service=None).onto(confluent_kafka.Consumer)


def unpatch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        setattr(confluent_kafka, "_datadog_patch", False)

    trace_utils.unwrap(TracedProducer, "produce")
    #trace_utils.unwrap(TracedConsumer, "consume")

    confluent_kafka.Producer = _original_kafka_producer
    confluent_kafka.Consumer = _original_kafka_consumer


def wrap_produce(func, instance, pin, integration_config, args, kwargs):
    topic = kwargs.get("topic")
    if not topic:
        topic = args[0]

    with pin.tracer.trace(
        "kafkafoo", service=trace_utils.ext_service(pin, integration_config), span_type="kafkabar"
    ) as span:
        span.set_tag_str(SPAN_KIND, "spankhind")
        span.set_tag_str(COMPONENT, integration_config.integration_name)
        span.set_tag_str(SPAN_KIND, "spankhind")
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, integration_config.get_analytics_sample_rate())
        return func(*args, **kwargs)


def wrap_poll(func, instance, pin, integration_config, args, kwargs):
    pass


"""
    if instance._current_consume_span:
        context.detach(instance._current_context_token)
        instance._current_context_token = None
        instance._current_consume_span.end()
        instance._current_consume_span = None

    with pin.tracer.start_as_current_span(
        "recv", end_on_exit=True, kind=trace.SpanKind.CONSUMER
    ):
        record = func(*args, **kwargs)
        if record:
            links = []
            ctx = propagate.extract(record.headers(), getter=_kafka_getter)
            if ctx:
                for item in ctx.values():
                    if hasattr(item, "get_span_context"):
                        links.append(Link(context=item.get_span_context()))

            instance._current_consume_span = tracer.start_span(
                name=f"{record.topic()} process",
                links=links,
                kind=SpanKind.CONSUMER,
            )

            _enrich_span(
                instance._current_consume_span,
                record.topic(),
                record.partition(),
                record.offset(),
                operation=MessagingOperationValues.PROCESS,
            )
    instance._current_context_token = context.attach(
        trace.set_span_in_context(instance._current_consume_span)
    )

    return record
"""
