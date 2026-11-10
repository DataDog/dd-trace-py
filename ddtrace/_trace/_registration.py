"""Lazy subscriber declarations owned by tracing initialization and product startup."""

from ddtrace.internal.core.subscriber import SubscriberBundle


WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=("WebFrameworkRequestSubscriber", "WebFrameworkRequestExceptionSubscriber"),
)
