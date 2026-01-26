import os
from time import time_ns
from typing import Dict

import confluent_kafka

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib import trace_utils
from ddtrace.contrib.events.kafka import KafkaMessagingConsumeEvent
from ddtrace.contrib.events.kafka import KafkaMessagingProduceEvent
from ddtrace.ext import kafka as kafkax
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.datastreams.kafka import KafkaDsmCommitEvent
from ddtrace.internal.datastreams.kafka import KafkaDsmConsumeEvent
from ddtrace.internal.datastreams.kafka import KafkaDsmProduceEvent
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.propagation.http import HTTPPropagator as Propagator

from .utils import _get_cluster_id


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
    topic = get_argument_value(args, kwargs, 0, "topic") or ""
    headers = get_argument_value(args, kwargs, 6, "headers", optional=True) or {}
    message_key = kwargs.get("key", "") or ""
    cluster_id = _get_cluster_id(instance, topic)
    value = get_argument_value(args, kwargs, 1, "value", True)
    if _SerializingProducer is not None and isinstance(instance, _SerializingProducer):
        serialized_key = serialize_key(instance, topic, message_key, headers)
        if serialized_key is not None:
            message_key = serialized_key

    with core.context_with_event(
        KafkaMessagingProduceEvent(
            config=config.kafka,
            operation=kafkax.PRODUCE,
            provider="kafka",
            topic=topic,
            bootstrap_servers=instance._dd_bootstrap_servers,
            messaging_system=kafkax.SERVICE,
            cluster_id=cluster_id,
            message_key=message_key,
            tombstone=str(value is None),
            value=value,
            partition=kwargs.get("partition", -1),
            headers=headers,
        ),
        service=trace_utils.ext_service(None, config.kafka),
    ) as ctx:
        args, kwargs = set_argument_value(args, kwargs, 6, "headers", headers, override_unset=True)

        core.dispatch_event(
            KafkaDsmProduceEvent(
                args=args,
                kwargs=kwargs,
                key=message_key,
                message=value,
                headers=headers,
                is_serializing=isinstance(instance, _SerializingProducer),
                topic=topic,
                cluster_id=cluster_id,
                span=ctx.span,
            ),
        )

        return func(*args, **kwargs)


def traced_poll_or_consume(func, instance, args, kwargs):
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
        if isinstance(result, confluent_kafka.Message):
            # poll returns a single message
            _instrument_message([result], start_ns, instance, err)
        elif isinstance(result, list):
            # consume returns a list of messages,
            _instrument_message(result, start_ns, instance, err)
        elif config.kafka.trace_empty_poll_enabled:
            _instrument_message([None], start_ns, instance, err)

    return result


def _instrument_message(messages, start_ns, instance, err):
    extracted_ctx = None
    # First message is used to extract context and enrich datadog spans
    # This approach aligns with the opentelemetry confluent kafka semantics
    first_message = messages[0] if len(messages) else None
    if first_message is not None and config.kafka.distributed_tracing_enabled and first_message.headers():
        extracted_ctx = Propagator.extract(dict(first_message.headers()))

    cluster_id = None
    topic = None
    message_key = None
    message_offset = None
    is_tombstone = None
    partition = None

    if first_message is not None:
        message_offset = first_message.offset() or -1
        topic = str(first_message.topic())
        cluster_id = _get_cluster_id(instance, str(first_message.topic()))
        partition = first_message.partition()

        # If this is a deserializing consumer, do not set the key as a tag since we
        # do not have the serialization function
        if (
            (_DeserializingConsumer is not None and not isinstance(instance, _DeserializingConsumer))
            or isinstance(message_key, str)
            or isinstance(message_key, bytes)
        ):
            message_key = first_message.key() or ""

        is_tombstone = False
        try:
            is_tombstone = len(first_message) == 0
        except TypeError:  # https://github.com/confluentinc/confluent-kafka-python/issues/1192
            pass

    with core.context_with_event(
        KafkaMessagingConsumeEvent(
            config=config.kafka,
            operation=kafkax.CONSUME,
            provider="kafka",
            messaging_system=kafkax.SERVICE,
            cluster_id=cluster_id,
            group_id=instance._group_id,
            topic=topic,
            is_tombstone=is_tombstone,
            message_offset=message_offset,
            message_key=ensure_text(message_key, errors="replace") if message_key is not None else None,
            received_message=str(first_message is not None),
            partition=partition,
            start_ns=start_ns,
            error=err,
        ),
        service=trace_utils.ext_service(None, config.kafka),
        call_trace=False,
        distributed_context=extracted_ctx
        if extracted_ctx is not None and extracted_ctx.trace_id is not None
        else tracer.context_provider.active(),
    ) as ctx:
        for message in messages:
            if message is not None and first_message is not None:
                core.dispatch_event(
                    KafkaDsmConsumeEvent(
                        message=message,
                        instance=instance,
                        kafka_topic=str(first_message.topic()),
                        cluster_id=cluster_id,
                        span=ctx.span,
                    )
                )


def traced_commit(func, instance, args, kwargs):
    message = get_argument_value(args, kwargs, 0, "message", optional=True)
    offsets = get_argument_value(args, kwargs, 1, "offsets", True) or []

    core.dispatch_event(KafkaDsmCommitEvent(instance, message, offsets))
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
