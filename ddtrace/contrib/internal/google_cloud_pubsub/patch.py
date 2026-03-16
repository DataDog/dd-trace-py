import importlib.metadata as importlib_metadata
import os

import google.cloud.pubsub_v1 as pubsub_v1
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


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
    return {"google.cloud.pubsub_v1": ">=2.10.0"}


def _parse_topic_path(topic):
    if not isinstance(topic, str):
        return "", ""
    parts = topic.split("/")
    project_id = parts[1] if len(parts) >= 2 else ""
    topic_id = parts[3] if len(parts) >= 4 else topic
    return project_id, topic_id


def patch():
    if getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = True

    _w("google.cloud.pubsub_v1.publisher.client", "Client.publish", _traced_publish)


def unpatch():
    if not getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = False

    _u(pubsub_v1.publisher.client.Client, "publish")


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
        service=ext_service(None, config.google_cloud_pubsub),
        resource=topic_id,
        call_trace=False,
        child_of=tracer.context_provider.active(),
        project_id=project_id,
        topic_id=topic_id,
        publish_kwargs=kwargs,
    ) as ctx:

        try:
            result = func(*args, **kwargs)
        except BaseException as e:
            core.dispatch("google_cloud_pubsub.send.completed", (ctx, (type(e), e, e.__traceback__), None))
            raise

        def sent_callback(future):
            try:
                message_id = future.result()
                core.dispatch("google_cloud_pubsub.send.completed", (ctx, (None, None, None), message_id))
            except Exception as e:
                core.dispatch("google_cloud_pubsub.send.completed", (ctx, (type(e), e, e.__traceback__), None))

        result.add_done_callback(sent_callback)
        return result
