import importlib.metadata as importlib_metadata
import os

from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_cloud_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import HTTPPropagator as Propagator
from ddtrace.trace import tracer


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
    from google.cloud.pubsub_v1.publisher.client import Client

    if getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = True

    _w("google.cloud.pubsub_v1.publisher.client", "Client.publish", _traced_publish)
    Pin().onto(Client)


def unpatch():
    import google.cloud.pubsub_v1 as pubsub_v1

    if not getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = False

    from google.cloud.pubsub_v1.publisher.client import Client

    trace_utils.unwrap(Client, "publish")


def _traced_publish(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    topic = args[0] if args else kwargs.get("topic", "")
    project_id, topic_id = _parse_topic_path(topic)

    with tracer.trace(
        name=schematize_cloud_messaging_operation(
            "gcp.pubsub.send",
            cloud_provider="gcp",
            cloud_service="pubsub",
            direction=SpanDirection.OUTBOUND,
        ),
        service=trace_utils.ext_service(pin, config.google_cloud_pubsub),
        span_type=SpanTypes.WORKER,
    ) as span:
        span._set_tag_str(COMPONENT, config.google_cloud_pubsub.integration_name)
        span._set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        span._set_tag_str("gcloud.project_id", project_id)
        span._set_tag_str(MESSAGING_SYSTEM, "pubsub")
        span._set_tag_str(MESSAGING_DESTINATION_NAME, topic_id)
        span._set_tag_str(MESSAGING_OPERATION, "send")
        span._set_tag_str("operation", "gcp.pubsub.send")
        span.set_metric(_SPAN_MEASURED_KEY, 1)
        span.resource = topic_id

        if config.google_cloud_pubsub.distributed_tracing_enabled:
            headers = {}
            Propagator.inject(span.context, headers)
            kwargs.update(headers)

        return func(*args, **kwargs)
