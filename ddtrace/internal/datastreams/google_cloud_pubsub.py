from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


log = get_logger(__name__)

# Reserved kwargs on Publisher.publish that are not message attributes.
_PUBLISH_RESERVED_KWARGS = frozenset({"data", "ordering_key", "retry", "timeout"})


def _extract_publish_attributes(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if k not in _PUBLISH_RESERVED_KWARGS}


def dsm_pubsub_send(args: tuple[Any, ...], kwargs: dict[str, Any], span: Optional[Any]) -> None:
    from . import data_streams_processor as processor

    topic = get_argument_value(args, kwargs, 0, "topic")
    data = get_argument_value(args, kwargs, 1, "data", optional=True)
    ordering_key = kwargs.get("ordering_key", "")
    attributes = _extract_publish_attributes(kwargs)

    payload_size = 0
    payload_size += _calculate_byte_size(data)
    payload_size += _calculate_byte_size(ordering_key)
    payload_size += _calculate_byte_size(attributes)

    edge_tags = ["direction:out", f"topic:{topic}", "type:google-pubsub"]
    ctx = processor().set_checkpoint(edge_tags, payload_size=payload_size, span=span)
    # Pub/Sub message attributes are passed as **kwargs to Publisher.publish().
    # Python's varkwargs accepts hyphenated keys like "dd-pathway-ctx-base64"
    # when unpacked, so injecting into the kwargs dict propagates the pathway
    # context to the broker. The existing distributed-tracing HTTPPropagator.inject
    # call uses the same mechanism (see _on_pubsub_send_start in trace_handlers.py).
    DsmPathwayCodec.encode(ctx, kwargs)


def dsm_pubsub_receive(subscription: str, message: Any, span: Optional[Any]) -> None:
    from . import data_streams_processor as processor

    attributes = dict(message.attributes) if message.attributes else {}

    payload_size = 0
    payload_size += _calculate_byte_size(getattr(message, "data", None))
    payload_size += _calculate_byte_size(getattr(message, "ordering_key", "") or "")
    payload_size += _calculate_byte_size(attributes)

    ctx = DsmPathwayCodec.decode(attributes, processor())
    # AIDEV-NOTE: dd-trace-py uses the `topic:` tag key as the generic destination
    # identifier for every messaging integration (Kafka, Kinesis, SQS, SNS, RabbitMQ).
    # The *value* on the consumer side is the Pub/Sub subscription path, which
    # preserves fan-out distinction (multiple subscriptions on the same topic each
    # produce a distinct pathway node) while keeping the tag schema consistent with
    # the rest of the Python DSM integrations. Note that dd-trace-java uses a
    # dedicated `subscription:` tag key here instead; the wire-format pathway hash
    # is unaffected by that difference.
    edge_tags = ["direction:in", f"topic:{subscription}", "type:google-pubsub"]
    ctx.set_checkpoint(edge_tags, payload_size=payload_size, span=span)


if config._data_streams_enabled:
    core.on("google_cloud_pubsub.send.pre", dsm_pubsub_send)
    core.on("google_cloud_pubsub.receive.pre", dsm_pubsub_receive)
