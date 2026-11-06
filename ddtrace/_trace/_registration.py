"""Lazy subscriber bundles owned by tracing."""

from ddtrace.internal.core.subscriber import SubscriberBundle


SPAN_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers._base",
    subscribers=("TracingSubscriber",),
)
HTTP_CLIENT_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.http_client",
    subscribers=("HttpClientTracingSubscriber",),
)
WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=("WebFrameworkRequestSubscriber",),
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

# Ray and AWS Durable subscribers load only when their integrations are patched.
SUBSCRIBER_BUNDLES = (
    WEB_FRAMEWORK_SUBSCRIBERS,
    SPAN_SUBSCRIBERS,
    HTTP_CLIENT_SUBSCRIBERS,
    MESSAGING_SUBSCRIBERS,
    KAFKA_SUBSCRIBERS,
    LLM_SUBSCRIBERS,
    MOLTEN_SUBSCRIBERS,
)
