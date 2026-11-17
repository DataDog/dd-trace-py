"""Lazy subscriber declarations owned by tracing initialization and product startup."""

from ddtrace.internal.core.subscriber import SubscriberBundle


WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=(
        "WebFrameworkRequestSubscriber",
        "WebFrameworkResponseSubscriber",
    ),
)


FLASK_REQUEST_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.flask",
    subscribers=("FlaskRequestSubscriber",),
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
    FLASK_REQUEST_SUBSCRIBERS,
    *MANUAL_SUBSCRIBER_BUNDLES,
)


def register_subscribers(bundles: tuple[SubscriberBundle, ...] = subscriber_bundles) -> None:
    """Activate tracing listeners for manual initialization and product startup."""
    for bundle in bundles:
        bundle.register()
