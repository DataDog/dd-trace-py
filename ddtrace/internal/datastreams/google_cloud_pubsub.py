from __future__ import annotations

from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.pipelines import get_pipelines_reporter
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
    if (p := processor()) is not None:
        ctx = p.set_checkpoint(edge_tags, payload_size=payload_size, span=span)
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

    if (p := processor()) is not None:
        ctx = DsmPathwayCodec.decode(attributes, p)
        # dd-trace-py uses the `topic:` tag key as the generic destination
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


# Automatic DSM pipelines (DSM2-531): service-topic sides and in-process links
# only, no pathway context and no stats. These subscriptions are unconditional;
# every handler bails out when get_pipelines_reporter() is None, so the feature
# is a no-op when pipelines reporting is disabled.

_PIPELINES_QUEUE_TYPE = "pubsub"

# Dispatched with the Subscription result of create/get/update subscription
# admin calls (see contrib/internal/google_cloud_pubsub/patch.py).
_PIPELINES_SUBSCRIPTION_RESULT_EVENT = "google_cloud_pubsub.subscription.result"

# Process-level cache: subscription path -> bound topic path. Consumers fall
# back to the subscription path itself when the bound topic is unknown.
_subscription_topic_cache: dict[str, str] = {}


def _extract_subscription_field(result: Any, field: str) -> Optional[str]:
    """Read field from a Subscription proto or a plain dict; empty/missing -> None."""
    if isinstance(result, dict):
        value = result.get(field)
    else:
        value = getattr(result, field, None)
    return value if isinstance(value, str) and value else None


def pipelines_pubsub_send(args: tuple[Any, ...], kwargs: dict[str, Any], span: Optional[Any] = None) -> None:
    try:
        reporter = get_pipelines_reporter()
        if reporter is None:
            return
        topic = get_argument_value(args, kwargs, 0, "topic")
        if topic:
            reporter.on_produce(_PIPELINES_QUEUE_TYPE, "", topic)
    except Exception:
        log.debug("pipelines: failed to record pubsub produce side", exc_info=True)


def pipelines_pubsub_receive(subscription: str, message: Optional[Any] = None, span: Optional[Any] = None) -> None:
    try:
        reporter = get_pipelines_reporter()
        if reporter is None:
            return
        if subscription:
            topic = _subscription_topic_cache.get(subscription, subscription)
            reporter.set_current_consume(_PIPELINES_QUEUE_TYPE, "", topic, subscription)
    except Exception:
        log.debug("pipelines: failed to record pubsub consume side", exc_info=True)


def pipelines_pubsub_subscription_result(result: Any) -> None:
    try:
        reporter = get_pipelines_reporter()
        if reporter is None:
            return
        name = _extract_subscription_field(result, "name")
        topic = _extract_subscription_field(result, "topic")
        if name and topic:
            _subscription_topic_cache[name] = topic
    except Exception:
        log.debug("pipelines: failed to cache pubsub subscription topic", exc_info=True)


core.on("google_cloud_pubsub.send.pre", pipelines_pubsub_send)
core.on("google_cloud_pubsub.receive.pre", pipelines_pubsub_receive)
core.on(_PIPELINES_SUBSCRIPTION_RESULT_EVENT, pipelines_pubsub_subscription_result)
