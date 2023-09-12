import time

from confluent_kafka import TopicPartition

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value


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
        try:
            on_delivery = get_argument_value(args, kwargs, on_delivery_arg, on_delivery_kwarg)
        except ArgumentError:
            on_delivery = None

    def wrapped_callback(err, msg):
        if err is None:
            processor().track_kafka_produce(msg.topic(), msg.partition(), msg.offset() or -1, time.time())
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
        processor().track_kafka_commit(
            instance._group_id, message.topic(), message.partition(), message.offset() or -1, time.time()
        )


def dsm_kafka_message_commit(instance, args, kwargs):
    from . import data_streams_processor as processor

    message = None
    try:
        message = get_argument_value(args, kwargs, 0, "message")
    except ArgumentError:
        pass  # set message to None

    offsets = kwargs.get("offsets", [])
    if message is not None:
        offsets = [TopicPartition(message.topic(), message.partition(), offset=message.offset())]
    for offset in offsets:
        processor().track_kafka_commit(
            instance._group_id, offset.topic, offset.partition, offset.offset or -1, time.time()
        )


if config._data_streams_enabled:
    core.on("kafka.produce.start", dsm_kafka_message_produce)
    core.on("kafka.consume.start", dsm_kafka_message_consume)
    core.on("kafka.commit.start", dsm_kafka_message_commit)
