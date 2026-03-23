from functools import partial
import importlib.metadata as importlib_metadata

import google.cloud.pubsub_v1 as pubsub_v1
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace import tracer
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator


config._add(
    "google_cloud_pubsub",
    dict(
        distributed_tracing_enabled=asbool(_get_config("DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_ENABLED", default=True)),
        propagation_as_span_links=asbool(
            _get_config("DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_AS_SPAN_LINKS", default=False)
        ),
    ),
)


def get_version() -> str:
    return str(importlib_metadata.version("google-cloud-pubsub"))


def _supported_versions() -> dict[str, str]:
    return {"google.cloud.pubsub_v1": ">=2.10.0"}


def _parse_resource_path(path):
    if not isinstance(path, str):
        return "", ""
    parts = path.split("/")
    project_id = parts[1] if len(parts) >= 2 else ""
    resource_id = parts[3] if len(parts) >= 4 else path
    return project_id, resource_id


def _traced_subscribe_callback(callback, project_id, subscription_id, message):
    propagated_context = None
    if config.google_cloud_pubsub.distributed_tracing_enabled and message.attributes:
        ctx = HTTPPropagator.extract(dict(message.attributes))
        if ctx.trace_id is not None and ctx.span_id is not None:
            propagated_context = ctx

    with core.context_with_data(
        "google_cloud_pubsub.receive",
        span_name="gcp.pubsub.receive",
        span_type=SpanTypes.WORKER,
        resource=subscription_id,
        call_trace=False,
        activate=True,
        child_of=propagated_context if not config.google_cloud_pubsub.propagation_as_span_links else None,
        propagated_context=propagated_context if config.google_cloud_pubsub.propagation_as_span_links else None,
        project_id=project_id,
        subscription_id=subscription_id,
        message=message,
    ):
        callback(message)


def patch():
    if getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = True

    _w("google.cloud.pubsub_v1.publisher.client", "Client.publish", _traced_publish)
    _w("google.cloud.pubsub_v1.subscriber.client", "Client.subscribe", _traced_subscribe)


def unpatch():
    if not getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = False

    _u(pubsub_v1.publisher.client.Client, "publish")
    _u(pubsub_v1.subscriber.client.Client, "subscribe")


def _traced_publish(func, instance, args, kwargs):
    topic = get_argument_value(args, kwargs, 0, "topic")
    project_id, topic_id = _parse_resource_path(topic)

    with core.context_with_data(
        "google_cloud_pubsub.send",
        span_name="gcp.pubsub.send",
        span_type=SpanTypes.WORKER,
        service=None,
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


def _traced_subscribe(func, instance, args, kwargs):
    subscription = get_argument_value(args, kwargs, 0, "subscription")
    callback = get_argument_value(args, kwargs, 1, "callback")
    project_id, subscription_id = _parse_resource_path(subscription)
    traced_callback = partial(_traced_subscribe_callback, callback, project_id, subscription_id)
    args, kwargs = set_argument_value(args, kwargs, 1, "callback", traced_callback)
    return func(*args, **kwargs)
