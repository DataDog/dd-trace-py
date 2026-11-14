"""Lazy subscriber declarations owned by tracing initialization and product startup."""

from ddtrace.internal.core.subscriber import SubscriberBundle


WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=("WebFrameworkRequestSubscriber",),
)


subscriber_bundles = (WEB_FRAMEWORK_SUBSCRIBERS,)


def register_subscribers() -> None:
    """Activate tracing listeners for manual initialization and product startup."""
    for bundle in subscriber_bundles:
        bundle.register()
