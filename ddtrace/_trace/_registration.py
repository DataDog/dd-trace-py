"""Lazy subscriber declarations shared by tracing startup and integration setup."""

from ddtrace.internal.core.subscriber import SubscriberBundle


WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=("WebFrameworkRequestSubscriber", "WebFrameworkRequestExceptionSubscriber"),
)
