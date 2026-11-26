import time
from typing import Any
from typing import Optional

from confluent_kafka import TopicPartition

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


INT_TYPES = (int,)
MESSAGE_ARG_POSITION = 1
KEY_ARG_POSITION = 2
KEY_KWARG_NAME = "key"
ON_DELIVERY_ARG_POSITION = 5
ON_DELIVERY_KWARG_NAME = "on_delivery"
CALLBACK_ARG_POSITION = 4
CALLBACK_KWARG_NAME = "callback"

disable_header_injection = False

log = get_logger(__name__)


def _headers_byte_size(headers: Any, exclude: Optional[str] = None) -> int:
    """Size of headers given as a dict or as confluent-kafka's list of (key, value) pairs."""
    entries = headers.items() if isinstance(headers, dict) else headers
    try:
        return sum(_calculate_byte_size(key) + _calculate_byte_size(value) for key, value in entries if key != exclude)
    except (TypeError, ValueError):
        return 0


def _with_pathway_header(headers: Any, ctx: Any) -> list[tuple[Any, Any]]:
    """A copy of the header list with any DSM pathway header replaced by one for ctx."""
    pathway_headers: dict[str, str] = {}
    DsmPathwayCodec.encode(ctx, pathway_headers)
    kept = [header for header in headers if header[0] not in pathway_headers]
    return kept + list(pathway_headers.items())


def dsm_kafka_message_produce(
    instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any], is_serializing: bool, span: Any
) -> None:
    from . import data_streams_processor as processor

    topic = core.find_item("kafka_topic")
    cluster_id = core.find_item("kafka_cluster_id")
    # Membership and length checks instead of get_argument_value: positional values would raise and catch.
    if "value" in kwargs:
        message = kwargs["value"]
    else:
        message = args[MESSAGE_ARG_POSITION] if len(args) > MESSAGE_ARG_POSITION else None
    if KEY_KWARG_NAME in kwargs:
        key = kwargs[KEY_KWARG_NAME]
    else:
        key = args[KEY_ARG_POSITION] if len(args) > KEY_ARG_POSITION else None
    headers = kwargs.get("headers")
    if headers is None:
        headers = {}

    payload_size = 0
    payload_size += _calculate_byte_size(message)
    payload_size += _calculate_byte_size(key)
    # set_checkpoint adds the size of the pathway header that replaces any forwarded one
    payload_size += _headers_byte_size(headers, exclude=PROPAGATION_KEY_BASE_64)

    edge_tags = ["direction:out", "topic:" + topic, "type:kafka"]
    if cluster_id:
        edge_tags.append("kafka_cluster_id:" + str(cluster_id))

    if (p := processor()) is not None:
        ctx = p.set_checkpoint(edge_tags, payload_size=payload_size, span=span)
        if not disable_header_injection:
            if isinstance(headers, dict):
                DsmPathwayCodec.encode(ctx, headers)
                kwargs["headers"] = headers
            elif isinstance(headers, (list, tuple)):
                kwargs["headers"] = _with_pathway_header(headers, ctx)

    on_delivery_kwarg = ON_DELIVERY_KWARG_NAME
    on_delivery_arg = ON_DELIVERY_ARG_POSITION
    if on_delivery_kwarg in kwargs:
        on_delivery = kwargs[on_delivery_kwarg]
    elif len(args) > on_delivery_arg:
        on_delivery = args[on_delivery_arg]
    elif is_serializing:
        on_delivery = None
    else:
        on_delivery_kwarg = CALLBACK_KWARG_NAME
        on_delivery_arg = CALLBACK_ARG_POSITION
        if on_delivery_kwarg in kwargs:
            on_delivery = kwargs[on_delivery_kwarg]
        else:
            on_delivery = args[on_delivery_arg] if len(args) > on_delivery_arg else None

    def wrapped_callback(err, msg):
        global disable_header_injection
        if err is None:
            reported_offset = msg.offset() if isinstance(msg.offset(), INT_TYPES) else -1
            if (p := processor()) is not None:
                p.track_kafka_produce(msg.topic(), msg.partition(), reported_offset, time.time(), cluster_id=cluster_id)
        elif err.code() == -1 and not disable_header_injection:
            disable_header_injection = True
            log.error(
                "Kafka Broker responded with UNKNOWN_SERVER_ERROR (-1). Please look at broker logs for more "
                "information. Tracer message header injection for Kafka is disabled."
            )
        if on_delivery is not None:
            on_delivery(err, msg)

    # A positional callback stays unwrapped: the hook cannot rebind the caller's args tuple.
    # Otherwise set it even if the client did not, to track produce calls correctly.
    if len(args) <= on_delivery_arg:
        kwargs[on_delivery_kwarg] = wrapped_callback


def dsm_kafka_message_consume(instance, message, span):
    from . import data_streams_processor as processor

    if message.error() is not None:
        return

    header_list = message.headers() or []
    headers = dict(header_list)
    topic = core.find_item("kafka_topic")
    cluster_id = core.find_item("kafka_cluster_id")
    group = instance._group_id

    payload_size = 0
    if hasattr(message, "len"):
        # message.len() is only supported for some versions of confluent_kafka
        payload_size += message.len()
    else:
        payload_size += _calculate_byte_size(message.value())

    payload_size += _calculate_byte_size(message.key())
    payload_size += _headers_byte_size(header_list)

    p = processor()
    if p is None:
        return

    ctx = DsmPathwayCodec.decode(headers, p)

    edge_tags = ["direction:in", "group:" + group, "topic:" + topic, "type:kafka"]
    if cluster_id:
        edge_tags.append("kafka_cluster_id:" + str(cluster_id))

    ctx.set_checkpoint(
        edge_tags,
        payload_size=payload_size,
        span=span,
    )

    if instance._auto_commit:
        # it's not exactly true, but if auto commit is enabled, we consider that a message is acknowledged
        # when it's read. We add one because the commit offset is the next message to read.
        reported_offset = (message.offset() + 1) if isinstance(message.offset(), INT_TYPES) else -1
        p.track_kafka_commit(
            instance._group_id,
            message.topic(),
            message.partition(),
            reported_offset,
            time.time(),
            cluster_id=cluster_id,
        )


def dsm_kafka_message_commit(instance, args, kwargs):
    from . import data_streams_processor as processor

    cluster_id = core.find_item("kafka_cluster_id") or ""
    message = get_argument_value(args, kwargs, 0, "message", optional=True)

    offsets = []
    if message is not None:
        # the commit offset is the next message to read. So last message read + 1
        reported_offset = message.offset() + 1 if isinstance(message.offset(), INT_TYPES) else -1
        offsets = [TopicPartition(message.topic(), message.partition(), reported_offset)]
    else:
        offsets = get_argument_value(args, kwargs, 1, "offsets", True) or []

    p = processor()
    if p is None:
        return

    for offset in offsets:
        reported_offset = offset.offset if isinstance(offset.offset, INT_TYPES) else -1
        p.track_kafka_commit(
            instance._group_id,
            offset.topic,
            offset.partition,
            reported_offset,
            time.time(),
            cluster_id=cluster_id,
        )


if config._data_streams_enabled:
    core.on("kafka.produce.start", dsm_kafka_message_produce)
    core.on("kafka.consume.start", dsm_kafka_message_consume)
    core.on("kafka.commit.start", dsm_kafka_message_commit)
