from functools import partial
import importlib.metadata as importlib_metadata
from typing import Any

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
# Format: method_name -> (resource_name, [attribute, path])

_PUBLISHER_ADMIN_METHODS: dict[str, tuple[str, list[str]]] = {
    "create_topic": ("createTopic", ["name"]),
    "update_topic": ("updateTopic", ["topic", "name"]),
    "get_topic": ("getTopic", ["topic"]),
    "list_topics": ("listTopics", ["project"]),
    "list_topic_subscriptions": ("listTopicSubscriptions", ["topic"]),
    "list_topic_snapshots": ("listTopicSnapshots", ["topic"]),
    "delete_topic": ("deleteTopic", ["topic"]),
    "detach_subscription": ("detachSubscription", ["subscription"]),
}

_SUBSCRIBER_ADMIN_METHODS: dict[str, tuple[str, list[str]]] = {
    "create_subscription": ("createSubscription", ["name"]),
    "get_subscription": ("getSubscription", ["subscription"]),
    "update_subscription": ("updateSubscription", ["subscription", "name"]),
    "list_subscriptions": ("listSubscriptions", ["project"]),
    "delete_subscription": ("deleteSubscription", ["subscription"]),
    "modify_push_config": ("modifyPushConfig", ["subscription"]),
    "get_snapshot": ("getSnapshot", ["snapshot"]),
    "list_snapshots": ("listSnapshots", ["project"]),
    "create_snapshot": ("createSnapshot", ["name"]),
    "update_snapshot": ("updateSnapshot", ["snapshot", "name"]),
    "delete_snapshot": ("deleteSnapshot", ["snapshot"]),
    "seek": ("seek", ["subscription"]),
}

_SCHEMA_METHODS: dict[str, tuple[str, list[str]]] = {
    "create_schema": ("createSchema", ["parent"]),
    "get_schema": ("getSchema", ["name"]),
    "list_schemas": ("listSchemas", ["parent"]),
    "delete_schema": ("deleteSchema", ["name"]),
    "validate_schema": ("validateSchema", ["parent"]),
    "validate_message": ("validateMessage", ["parent"]),
}

# These methods do not exist in older SDK versions (<2.16.0).
_SCHEMA_OPTIONAL_METHODS: dict[str, tuple[str, list[str]]] = {
    "list_schema_revisions": ("listSchemaRevisions", ["name"]),
    "commit_schema": ("commitSchema", ["name"]),
    "rollback_schema": ("rollbackSchema", ["name"]),
    "delete_schema_revision": ("deleteSchemaRevision", ["name"]),
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


def _get_nested_attr(obj: object, parts: list[str]):
    """Walk `parts` to extract a nested value from a proto message or plain dict

    Each step in the for loop resolves one level of nesting
    * obj.get(part) for dicts
    * getattr(obj, part) for proto messages

    This is because GAPIC client methods accept both forms as requests
    Returns None if any intermediate level is missing.

    Example:
      _get_nested_attr({"topic": {"name": "{topicName}"}}, ["topic", "name"])
      _get_nested_attr(UpdateTopicRequest(topic=Topic(name="topicName")), ["topic", "name"])
      return => "topicName"
    """
    for part in parts:
        if obj is None:
            break
        obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
    return obj


def _make_admin_wrapper(resource_name: str, request_attr: list[str]):
    """Factory that creates a traced wrapper for a Pub/Sub admin/management method."""

    def wrapper(func, instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]):
        # GAPIC methods support three calling conventions:
        # 1. create_topic(request=CreateTopicRequest(name="...")) - supported by get_argument_value
        # 2. create_topic(CreateTopicRequest(name="..."))         - supported by get_argument_value
        # 3. create_topic(name="...")                             - fall back to kwargs
        request = get_argument_value(args, kwargs, 0, "request", optional=True) or kwargs
        resource_path = _get_nested_attr(request, request_attr) or ""
        project_id, _ = parse_resource_path(resource_path)
        resource = f"{resource_name} {resource_path}" if resource_path else resource_name

        with core.context_with_data(
            "google_cloud_pubsub.request",
            span_name="gcp.pubsub.request",
            span_type=SpanTypes.WORKER,
            service=None,
            resource=resource,
            project_id=project_id,
            pubsub_method=resource_name,
            measured=True,
        ):
            return func(*args, **kwargs)

    return wrapper


def _wrap_methods(module_path: str, cls: type[Any], methods: dict[str, tuple[str, list[str]]], optional: bool = False):
    """Wrap multiple methods on a class. Skip missing methods when optional=True."""
    for method_name, (resource_name, request_attr) in methods.items():
        if optional and not hasattr(cls, method_name):
            continue
        _w(
            module_path,
            f"{cls.__name__}.{method_name}",
            _make_admin_wrapper(resource_name, request_attr),
        )


def _unwrap_methods(cls: type[Any], methods: dict[str, tuple[str, list[str]]], optional: bool = False):
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
