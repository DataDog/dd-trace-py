import importlib.metadata as importlib_metadata
import os

from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.formats import asbool


config._add(
    "google_cloud_pubsub",
    dict(
        _default_service=schematize_service_name("google_cloud_pubsub"),
        distributed_tracing_enabled=asbool(os.getenv("DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_ENABLED", default=True)),
    ),
)


def get_version() -> str:
    return str(importlib_metadata.version("google-cloud-pubsub"))


def _supported_versions() -> dict[str, str]:
    return {"google.cloud.pubsub_v1": ">=2.25.0"}


def _parse_topic_path(topic):
    if not isinstance(topic, str):
        return "", ""
    parts = topic.split("/")
    project_id = parts[1] if len(parts) >= 2 else ""
    topic_id = parts[3] if len(parts) >= 4 else topic
    return project_id, topic_id


def patch():
    import google.cloud.pubsub_v1 as pubsub_v1

    if getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = True

    _w("google.cloud.pubsub_v1.publisher.client", "Client.publish", _traced_publish)


def unpatch():
    import google.cloud.pubsub_v1 as pubsub_v1

    if not getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = False

    from google.cloud.pubsub_v1.publisher.client import Client

    trace_utils.unwrap(Client, "publish")


def _traced_publish(func, instance, args, kwargs):
    topic = args[0] if args else kwargs.get("topic", "")
    project_id, topic_id = _parse_topic_path(topic)

    with core.context_with_data(
        "google_cloud_pubsub.send",
        span_name=schematize_cloud_messaging_operation(
            "gcp.pubsub.send",
            cloud_provider="gcp",
            cloud_service="pubsub",
            direction=SpanDirection.OUTBOUND,
        ),
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.google_cloud_pubsub),
        resource=topic_id,
    ) as ctx:
        core.dispatch("google_cloud_pubsub.send.start", (ctx, project_id, topic_id, kwargs))
        return func(*args, **kwargs)
