"""Tracing's declarations for explicitly managed subscriber bundles."""

from ddtrace.internal.products import ProductRegistration
from ddtrace.internal.products import SubscriberBundle


registration = ProductRegistration(
    name="tracer",
    subscriber_bundles={
        "web_framework": SubscriberBundle(
            module="ddtrace._trace.subscribers.web_framework",
            subscribers=("WebFrameworkRequestSubscriber",),
        ),
    },
)
