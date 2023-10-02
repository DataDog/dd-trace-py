import time

from confluent_kafka import TopicPartition
import six

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value


INT_TYPES = six.integer_types


def dsm_kafka_message_produce(instance, args, kwargs):
    from . import data_streams_processor as processor

    topic = core.get_item("kafka_topic")
    pathway = processor().set_checkpoint(["direction:out", "topic:" + topic, "type:kafka"])
    encoded_pathway = pathway.encode()
    headers = kwargs.get("headers", {})
    headers[PROPAGATION_KEY] = encoded_pathway
    kwargs["headers"] = headers

    on_delivery_kwarg = "on_delivery"
    on_delivery_arg = 5
    on_delivery = None
    try:
        on_delivery = get_argument_value(args, kwargs, on_delivery_arg, on_delivery_kwarg)
    except ArgumentError:
        on_delivery_kwarg = "callback"
        on_delivery_arg = 4
        on_delivery = get_argument_value(args, kwargs, on_delivery_arg, on_delivery_kwarg, optional=True)

    def wrapped_callback(err, msg):
        if err is None:
            reported_offset = msg.offset() if isinstance(msg.offset(), INT_TYPES) else -1
            processor().track_kafka_produce(msg.topic(), msg.partition(), reported_offset, time.time())
        if on_delivery is not None:
            on_delivery(err, msg)

    try:
        args, kwargs = set_argument_value(args, kwargs, on_delivery_arg, on_delivery_kwarg, wrapped_callback)
    except ArgumentError:
        # we set the callback even if it's not set by the client, to track produce calls correctly.
        kwargs[on_delivery_kwarg] = wrapped_callback


def dsm_kafka_message_consume(instance, message):
    from . import data_streams_processor as processor

    headers = {header[0]: header[1] for header in (message.headers() or [])}
    topic = core.get_item("kafka_topic")
    group = instance._group_id

    ctx = processor().decode_pathway(headers.get(PROPAGATION_KEY, None))
    ctx.set_checkpoint(["direction:in", "group:" + group, "topic:" + topic, "type:kafka"])

    if instance._auto_commit:
        # it's not exactly true, but if auto commit is enabled, we consider that a message is acknowledged
        # when it's read.
        reported_offset = message.offset() if isinstance(message.offset(), INT_TYPES) else -1
        processor().track_kafka_commit(
            instance._group_id, message.topic(), message.partition(), reported_offset, time.time()
        )


def dsm_kafka_message_commit(instance, args, kwargs):
    from . import data_streams_processor as processor

    message = get_argument_value(args, kwargs, 0, "message", optional=True)

    offsets = []
    if message is not None:
        # We need to add one to message offsets to make them mean the same thing as offsets
        # passed in by the offsets keyword
        reported_offset = message.offset() + 1 if isinstance(message.offset(), INT_TYPES) else -1
        offsets = [TopicPartition(message.topic(), message.partition(), reported_offset)]
    else:
        offsets = get_argument_value(args, kwargs, 1, "offsets", True) or []

    for offset in offsets:
        # When offsets is passed in as an arg, its an exact value for the next expected message.
        # When message is passed in Kafka reports msg.offset() + 1.  We add +1 above to message
        # offsets to make them mean the same thing as passed in offsets, then subtract 1 universally
        # here from both
        reported_offset = offset.offset - 1 if isinstance(offset.offset, INT_TYPES) else -1
        processor().track_kafka_commit(instance._group_id, offset.topic, offset.partition, reported_offset, time.time())


if config._data_streams_enabled:
    core.on("kafka.produce.start", dsm_kafka_message_produce)
    core.on("kafka.consume.start", dsm_kafka_message_consume)
    core.on("kafka.commit.start", dsm_kafka_message_commit)
