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


SPAN_EXCEPTION_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers._base",
    subscribers=("SpanExceptionSubscriber",),
)


subscriber_bundles = (WEB_FRAMEWORK_SUBSCRIBERS, FLASK_REQUEST_SUBSCRIBERS, SPAN_EXCEPTION_SUBSCRIBERS)


def register_subscribers() -> None:
    """Activate tracing listeners for manual initialization and product startup."""
    for bundle in subscriber_bundles:
        bundle.register()
