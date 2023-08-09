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
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.pin import Pin


_Producer = confluent_kafka.Producer
_Consumer = confluent_kafka.Consumer


config._add(
    "kafka",
    dict(
        _default_service=schematize_service_name("kafka"),
    ),
)


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

    topic = get_argument_value(args, kwargs, 0, "topic") or ""
    try:
        value = get_argument_value(args, kwargs, 1, "value")
    except ArgumentError:
        value = None
    message_key = kwargs.get("key", "")
    partition = kwargs.get("partition", -1)
    if config._data_streams_enabled:
        # inject data streams context
        headers = kwargs.get("headers", {})
        pathway = pin.tracer.data_streams_processor.set_checkpoint(["direction:out", "topic:" + topic, "type:kafka"])
        headers[PROPAGATION_KEY] = pathway.encode()
        kwargs["headers"] = headers

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
                headers = {header[0]: header[1] for header in (message.headers() or [])}
                ctx = pin.tracer.data_streams_processor.decode_pathway(headers.get(PROPAGATION_KEY, None))
                ctx.set_checkpoint(
                    ["direction:in", "group:" + instance._group_id, "topic:" + message.topic(), "type:kafka"]
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
