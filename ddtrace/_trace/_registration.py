"""Lazy subscriber declarations owned by tracing initialization and product startup."""

import sys

from ddtrace.internal.core.subscriber import SubscriberBundle


WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=(
        "WebFrameworkRequestSubscriber",
        "WSGIRequestSubscriber",
        "WSGIResponseSubscriber",
    ),
)

SPAN_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers._base",
    subscribers=("TracingSubscriber",),
)
HTTP_CLIENT_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.http_client",
    subscribers=("HttpClientTracingSubscriber",),
)
MESSAGING_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.messaging",
    subscribers=("MessagingProduceSubscriber", "MessagingConsumeSubscriber"),
)
KAFKA_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.kafka",
    subscribers=("KafkaProduceSubscriber", "KafkaConsumeSubscriber"),
)
LLM_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.llm",
    subscribers=("LlmTracingSubscriber",),
)
MOLTEN_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.molten",
    subscribers=("MoltenRouteSubscriber",),
)
AWS_DURABLE_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.aws_durable",
    subscribers=("AwsDurableExecuteSubscriber", "AwsDurableInvokeSubscriber", "AwsDurableOperationSubscriber"),
)


# Ray subscribers depend on its optional SDK and are activated only once the
# integration's patch module has loaded its configuration and dependencies.
RAY_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.ray",
    subscribers=(
        "RayJobStartSubscriber",
        "RayExecutionSubscriber",
        "RayCoreAPITracingSubscriber",
        "RaySubmissionSubscriber",
        "RayContextInjectionSubscriber",
    ),
)


SPAN_EXCEPTION_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers._base",
    subscribers=("SpanExceptionSubscriber",),
)


MANUAL_SUBSCRIBER_BUNDLES = (
    SPAN_SUBSCRIBERS,
    HTTP_CLIENT_SUBSCRIBERS,
    MESSAGING_SUBSCRIBERS,
    KAFKA_SUBSCRIBERS,
    LLM_SUBSCRIBERS,
    MOLTEN_SUBSCRIBERS,
    AWS_DURABLE_SUBSCRIBERS,
)


subscriber_bundles = (
    WEB_FRAMEWORK_SUBSCRIBERS,
    SPAN_EXCEPTION_SUBSCRIBERS,
    *MANUAL_SUBSCRIBER_BUNDLES,
)


def register_subscribers(bundles: tuple[SubscriberBundle, ...] = subscriber_bundles) -> None:
    """Activate tracing listeners for manual initialization and product startup."""
    for bundle in bundles:
        bundle.register()

    # Restore SDK-dependent listeners only after the patch module finishes loading.
    # Modules appear in sys.modules before their SDK imports and configuration complete.
    ray_patch_name = "ddtrace.contrib.internal.ray.patch"
    ray_patch = sys.modules.get(ray_patch_name)
    if (
        ray_patch is not None
        and not getattr(ray_patch.__spec__, "_initializing", False)
        and sys.modules.get(ray_patch_name) is ray_patch
    ):
        RAY_SUBSCRIBERS.register()
