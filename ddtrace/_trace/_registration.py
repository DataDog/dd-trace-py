"""Tracing's declarations for explicitly managed subscriber bundles."""

from ddtrace.internal.products import SubscriberBundle


WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=("WebFrameworkRequestSubscriber",),
)
