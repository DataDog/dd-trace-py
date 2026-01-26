"""
Kafka integration using the unified integration tracing system.

This integration patches confluent_kafka to emit integration events
that are processed by hooks for context propagation and span tagging.
"""

import os
from time import time
from time import time_ns
from typing import Dict

import confluent_kafka

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.ext import kafka as kafkax
from ddtrace.internal import core
from ddtrace.internal.core.integration import IntegrationDescriptor
from ddtrace.internal.core.integration import IntegrationEvent
from ddtrace.internal.core.integration import SpanConfig
from ddtrace.internal.core.integration import dispatch_integration_event
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version

from .carrier import KafkaCarrierAdapter


_Producer = confluent_kafka.Producer
_Consumer = confluent_kafka.Consumer
_SerializingProducer = confluent_kafka.SerializingProducer if hasattr(confluent_kafka, "SerializingProducer") else None
_DeserializingConsumer = (
    confluent_kafka.DeserializingConsumer if hasattr(confluent_kafka, "DeserializingConsumer") else None
)


log = get_logger(__name__)


config._add(
    "kafka",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(os.getenv("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
        trace_empty_poll_enabled=asbool(os.getenv("DD_KAFKA_EMPTY_POLL_ENABLED", default=True)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(confluent_kafka, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"confluent_kafka": ">=1.9.2"}


KAFKA_VERSION_TUPLE = parse_version(get_version())


_SerializationContext = confluent_kafka.serialization.SerializationContext if KAFKA_VERSION_TUPLE >= (1, 4, 0) else None
_MessageField = confluent_kafka.serialization.MessageField if KAFKA_VERSION_TUPLE >= (1, 4, 0) else None


# Integration descriptors
KAFKA_PRODUCER = IntegrationDescriptor(
    name="kafka",
    category="messaging",
    role="producer",
    carrier_adapter=KafkaCarrierAdapter,
)

KAFKA_CONSUMER = IntegrationDescriptor(
    name="kafka",
    category="messaging",
    role="consumer",
    carrier_adapter=KafkaCarrierAdapter,
)


class TracedProducerMixin:
    def __init__(self, config=None, *args, **kwargs):
        if not config:
            config = kwargs
        super(TracedProducerMixin, self).__init__(config, *args, **kwargs)
        self._dd_bootstrap_servers = (
            config.get("bootstrap.servers")
            if config.get("bootstrap.servers") is not None
            else config.get("metadata.broker.list")
        )

    # in older versions of confluent_kafka, bool(Producer()) evaluates to False,
    # which makes the Pin functionality ignore it.
    def __bool__(self):
        return True

    __nonzero__ = __bool__


class TracedConsumerMixin:
    def __init__(self, config=None, *args, **kwargs):
        if not config:
            config = kwargs
        super(TracedConsumerMixin, self).__init__(config, *args, **kwargs)
        self._group_id = config.get("group.id", "")
        self._auto_commit = asbool(config.get("enable.auto.commit", True))


class TracedConsumer(TracedConsumerMixin, confluent_kafka.Consumer):
    pass


class TracedProducer(TracedProducerMixin, confluent_kafka.Producer):
    pass


class TracedDeserializingConsumer(TracedConsumerMixin, confluent_kafka.DeserializingConsumer):
    pass


class TracedSerializingProducer(TracedProducerMixin, confluent_kafka.SerializingProducer):
    pass


def patch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        return
    confluent_kafka._datadog_patch = True

    confluent_kafka.Producer = TracedProducer
    confluent_kafka.Consumer = TracedConsumer
    if _SerializingProducer is not None:
        confluent_kafka.SerializingProducer = TracedSerializingProducer
    if _DeserializingConsumer is not None:
        confluent_kafka.DeserializingConsumer = TracedDeserializingConsumer

    for producer in (TracedProducer, TracedSerializingProducer):
        trace_utils.wrap(producer, "produce", traced_produce)
    for consumer in (TracedConsumer, TracedDeserializingConsumer):
        trace_utils.wrap(consumer, "poll", traced_poll_or_consume)
        trace_utils.wrap(consumer, "commit", traced_commit)

    # Consume is not implemented in deserializing consumers
    trace_utils.wrap(TracedConsumer, "consume", traced_poll_or_consume)
    Pin().onto(confluent_kafka.Producer)
    Pin().onto(confluent_kafka.Consumer)
    Pin().onto(confluent_kafka.SerializingProducer)
    Pin().onto(confluent_kafka.DeserializingConsumer)


def unpatch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        confluent_kafka._datadog_patch = False

    for producer in (TracedProducer, TracedSerializingProducer):
        if trace_utils.iswrapped(producer.produce):
            trace_utils.unwrap(producer, "produce")
    for consumer in (TracedConsumer, TracedDeserializingConsumer):
        if trace_utils.iswrapped(consumer.poll):
            trace_utils.unwrap(consumer, "poll")
        if trace_utils.iswrapped(consumer.commit):
            trace_utils.unwrap(consumer, "commit")

    # Consume is not implemented in deserializing consumers
    if trace_utils.iswrapped(TracedConsumer.consume):
        trace_utils.unwrap(TracedConsumer, "consume")

    confluent_kafka.Producer = _Producer
    confluent_kafka.Consumer = _Consumer
    if _SerializingProducer is not None:
        confluent_kafka.SerializingProducer = _SerializingProducer
    if _DeserializingConsumer is not None:
        confluent_kafka.DeserializingConsumer = _DeserializingConsumer


def traced_produce(func, instance, args, kwargs):
    """Traced produce - emits facts only, hooks handle tagging and context propagation."""
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    topic = get_argument_value(args, kwargs, 0, "topic") or ""
    value = get_argument_value(args, kwargs, 1, "value", optional=True)
    message_key = kwargs.get("key", "") or ""
    partition = kwargs.get("partition", -1)
    headers = get_argument_value(args, kwargs, 6, "headers", optional=True) or {}
    is_serializing = _SerializingProducer is not None and isinstance(instance, _SerializingProducer)

    if is_serializing:
        message_key = serialize_key(instance, topic, message_key, headers) or message_key

    event = IntegrationEvent(
        integration=KAFKA_PRODUCER,
        event_type="send",
        span_config=SpanConfig(
            name=schematize_messaging_operation(kafkax.PRODUCE, provider="kafka", direction=SpanDirection.OUTBOUND),
            service=trace_utils.ext_service(pin, config.kafka),
            span_type=SpanTypes.WORKER,
        ),
        payload={
            "topic": topic,
            "partition": partition,
            "message_key": message_key,
            "headers": headers,
            "args": args,
            "kwargs": kwargs,
            "span_tags": {
                kafkax.TOPIC: topic,
                kafkax.PARTITION: partition,
                kafkax.TOMBSTONE: str(value is None),
                kafkax.MESSAGE_KEY: str(message_key) if message_key else None,
                kafkax.HOST_LIST: instance._dd_bootstrap_servers,
                kafkax.CLUSTER_ID: _get_cluster_id(instance, topic),
            },
            "span_metrics": {_SPAN_MEASURED_KEY: 1},
            "instance": instance,
            "is_serializing_producer": is_serializing,
        },
    )

    with dispatch_integration_event(event) as evt:
        core.dispatch("kafka.produce.start", (instance, args, kwargs, is_serializing, evt.span))
        return func(*evt.payload.get("args", args), **evt.payload.get("kwargs", kwargs))


def traced_poll_or_consume(func, instance, args, kwargs):
    """
    Traced poll/consume - emits facts only, no tracer logic.
    """
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # Capture start time before calling the function
    # (we need the message result to extract headers for context propagation,
    # but the span should start from when we began the operation)
    start_ns = time_ns()

    # Execute the function first to get the result
    err = None
    result = None
    try:
        result = func(*args, **kwargs)
    except Exception as e:
        err = e
        raise
    finally:
        # Determine what we got
        messages = []
        if isinstance(result, confluent_kafka.Message):
            messages = [result]
        elif isinstance(result, list):
            messages = result
        elif config.kafka.trace_empty_poll_enabled:
            messages = [None]

        if messages:
            _instrument_message(messages, pin, instance, start_ns, error=err)

    return result


def _instrument_message(messages, pin, instance, start_ns, error=None):
    """Instrument consumed messages using the integration system."""
    msg = messages[0] if messages else None
    received = msg is not None

    # Extract message facts (None if no message)
    topic = str(msg.topic()) if received else ""
    partition = msg.partition() if received else None
    offset = (msg.offset() or -1) if received else None
    message_key = _extract_message_key(msg, instance) if received else None
    is_tombstone = _is_tombstone(msg) if received else False

    event = IntegrationEvent(
        integration=KAFKA_CONSUMER,
        event_type="consume",
        span_config=SpanConfig(
            name=schematize_messaging_operation(kafkax.CONSUME, provider="kafka", direction=SpanDirection.PROCESSING),
            service=trace_utils.ext_service(pin, config.kafka),
            span_type=SpanTypes.WORKER,
            start_ns=start_ns,
        ),
        payload={
            "topic": topic,
            "partition": partition,
            "offset": offset,
            "message_key": message_key,
            "group_id": instance._group_id,
            "messages": messages,
            "span_tags": {
                kafkax.TOPIC: topic or None,
                kafkax.PARTITION: partition,
                kafkax.MESSAGE_OFFSET: offset,
                kafkax.MESSAGE_KEY: str(message_key) if message_key else None,
                kafkax.CLUSTER_ID: _get_cluster_id(instance, topic) if received else None,
                kafkax.GROUP_ID: instance._group_id,
                kafkax.RECEIVED_MESSAGE: str(received),
                kafkax.TOMBSTONE: str(is_tombstone),
            },
            "span_metrics": {_SPAN_MEASURED_KEY: 1},
            "instance": instance,
            "error": error,
        },
    )

    with dispatch_integration_event(event) as evt:
        for message in messages:
            if message is not None:
                core.dispatch("kafka.consume.start", (instance, message, evt.span))


def _extract_message_key(msg, instance):
    """Extract message key, handling deserializing consumer specially."""
    raw_key = msg.key() or ""
    if (_DeserializingConsumer is not None and not isinstance(instance, _DeserializingConsumer)) or isinstance(
        raw_key, (str, bytes)
    ):
        return raw_key
    return None


def _is_tombstone(msg):
    """Check if message is a tombstone (empty body)."""
    try:
        return len(msg) == 0
    except TypeError:  # https://github.com/confluentinc/confluent-kafka-python/issues/1192
        return False


def traced_commit(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    core.dispatch("kafka.commit.start", (instance, args, kwargs))
    return func(*args, **kwargs)


def serialize_key(instance, topic, key, headers):
    if _SerializationContext is not None and _MessageField is not None:
        ctx = _SerializationContext(topic, _MessageField.KEY, headers)
        if hasattr(instance, "_key_serializer") and instance._key_serializer is not None:
            try:
                key = instance._key_serializer(key, ctx)
                return key
            except Exception:
                log.debug("Failed to set Kafka Consumer key tag: %s", str(key))
                return None
        else:
            log.warning("Failed to set Kafka Consumer key tag, no method available to serialize key: %s", str(key))
            return None


def _get_cluster_id(instance, topic):
    # Check success cache
    if instance and getattr(instance, "_dd_cluster_id", None):
        return instance._dd_cluster_id

    # Check failure cache - skip for 5 minutes if we fail
    last_failure = getattr(instance, "_dd_cluster_id_failure_time", 0)
    if time() - last_failure < 300:
        return None

    if getattr(instance, "list_topics", None) is None:
        return None

    try:
        cluster_metadata = instance.list_topics(topic=topic, timeout=1.0)
        if cluster_metadata and getattr(cluster_metadata, "cluster_id", None):
            instance._dd_cluster_id = cluster_metadata.cluster_id
            return cluster_metadata.cluster_id
    except Exception:
        # Cache the failure time to avoid repeated slow calls
        instance._dd_cluster_id_failure_time = time()
        log.debug("Failed to get Kafka cluster ID, will retry after 5 minutes")

    return None
