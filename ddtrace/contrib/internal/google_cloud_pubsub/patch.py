from functools import partial
import importlib.metadata as importlib_metadata

import google.cloud.pubsub_v1 as pubsub_v1
from google.pubsub_v1.services.publisher.client import PublisherClient as GapicPublisher
from google.pubsub_v1.services.schema_service.client import SchemaServiceClient
from google.pubsub_v1.services.subscriber.client import SubscriberClient as GapicSubscriber
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.google_cloud_pubsub.utils import ensure_config_registered
from ddtrace.contrib.internal.google_cloud_pubsub.utils import parse_resource_path
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator


ensure_config_registered()


# Method descriptors for admin/management operations.
# Format: method_name -> dotted attr path on the request proto/dict/kwargs

_PUBLISHER_ADMIN_METHODS = {
    "create_topic": "name",
    "update_topic": "topic.name",
    "get_topic": "topic",
    "list_topics": "project",
    "list_topic_subscriptions": "topic",
    "list_topic_snapshots": "topic",
    "delete_topic": "topic",
    "detach_subscription": "subscription",
}

_SUBSCRIBER_ADMIN_METHODS = {
    "create_subscription": "name",
    "get_subscription": "subscription",
    "update_subscription": "subscription.name",
    "list_subscriptions": "project",
    "delete_subscription": "subscription",
    "modify_push_config": "subscription",
    "get_snapshot": "snapshot",
    "list_snapshots": "project",
    "create_snapshot": "name",
    "update_snapshot": "snapshot.name",
    "delete_snapshot": "snapshot",
    "seek": "subscription",
}

_SCHEMA_METHODS = {
    "create_schema": "parent",
    "get_schema": "name",
    "list_schemas": "parent",
    "delete_schema": "name",
    "validate_schema": "parent",
    "validate_message": "parent",
}

# These methods do not exist in older SDK versions (<2.16.0).
_SCHEMA_OPTIONAL_METHODS = {
    "list_schema_revisions": "name",
    "commit_schema": "name",
    "rollback_schema": "name",
    "delete_schema_revision": "name",
}


def get_version() -> str:
    return str(importlib_metadata.version("google-cloud-pubsub"))


def _supported_versions() -> dict[str, str]:
    return {"google.cloud.pubsub_v1": ">=2.10.0"}


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


def _snake_to_camel(name):
    """Convert snake_case to camelCase.

    Uses camelCase for consistency with other Datadog tracers (Node).
    """
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _get_nested_attr(obj, parts):
    """
    Walk an attribute path on an object or a plain dict.
    """
    for part in parts:
        if obj is None:
            break
        obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
    return obj


def _make_admin_wrapper(method_name, attr_path):
    """Factory that creates a traced wrapper for a Pub/Sub admin/management method."""
    camel_name = _snake_to_camel(method_name)
    parts = attr_path.split(".")

    def wrapper(func, instance, args, kwargs):
        # GAPIC methods support three calling conventions:
        # 1. create_topic(request=CreateTopicRequest(name="...")) - supported by get_argument_value
        # 2. create_topic(CreateTopicRequest(name="..."))         - supported by get_argument_value
        # 3. create_topic(name="...")                             - fall back to kwargs
        request = get_argument_value(args, kwargs, 0, "request", optional=True) or kwargs
        resource_path = _get_nested_attr(request, parts) or ""
        project_id, _ = parse_resource_path(resource_path)
        resource = "{} {}".format(camel_name, resource_path) if resource_path else camel_name

        with core.context_with_data(
            "google_cloud_pubsub.request",
            span_name="gcp.pubsub.request",
            span_type=SpanTypes.WORKER,
            service=None,
            resource=resource,
            project_id=project_id,
            pubsub_method=camel_name,
        ):
            return func(*args, **kwargs)

    return wrapper


def _wrap_methods(module_path, cls, methods, optional=False):
    """Wrap multiple methods on a class. Skip missing methods when optional=True."""
    for method_name, attr_path in methods.items():
        if optional and not hasattr(cls, method_name):
            continue
        _w(
            module_path,
            "{}.{}".format(cls.__name__, method_name),
            _make_admin_wrapper(method_name, attr_path),
        )


def _unwrap_methods(cls, methods, optional=False):
    """Unwrap multiple methods on a class."""
    for method_name in methods:
        if optional and not hasattr(cls, method_name):
            continue
        attr = getattr(cls, method_name, None)
        if attr is not None and hasattr(attr, "__wrapped__"):
            _u(cls, method_name)


def patch():
    if getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = True

    _w("google.cloud.pubsub_v1.publisher.client", "Client.publish", _traced_publish)
    _w("google.cloud.pubsub_v1.subscriber.client", "Client.subscribe", _traced_subscribe)

    _wrap_methods("google.pubsub_v1.services.publisher.client", GapicPublisher, _PUBLISHER_ADMIN_METHODS)
    _wrap_methods("google.pubsub_v1.services.subscriber.client", GapicSubscriber, _SUBSCRIBER_ADMIN_METHODS)
    _wrap_methods("google.pubsub_v1.services.schema_service.client", SchemaServiceClient, _SCHEMA_METHODS)
    _wrap_methods(
        "google.pubsub_v1.services.schema_service.client",
        SchemaServiceClient,
        _SCHEMA_OPTIONAL_METHODS,
        optional=True,
    )


def unpatch():
    if not getattr(pubsub_v1, "_datadog_patch", False):
        return
    pubsub_v1._datadog_patch = False

    _u(pubsub_v1.publisher.client.Client, "publish")
    _u(pubsub_v1.subscriber.client.Client, "subscribe")

    _unwrap_methods(GapicPublisher, _PUBLISHER_ADMIN_METHODS)
    _unwrap_methods(GapicSubscriber, _SUBSCRIBER_ADMIN_METHODS)
    _unwrap_methods(SchemaServiceClient, _SCHEMA_METHODS)
    _unwrap_methods(SchemaServiceClient, _SCHEMA_OPTIONAL_METHODS, optional=True)


def _traced_publish(func, instance, args, kwargs):
    topic = get_argument_value(args, kwargs, 0, "topic")
    project_id, topic_id = parse_resource_path(topic)

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
    project_id, subscription_id = parse_resource_path(subscription)
    traced_callback = partial(_traced_subscribe_callback, callback, project_id, subscription_id)
    args, kwargs = set_argument_value(args, kwargs, 1, "callback", traced_callback)
    return func(*args, **kwargs)
