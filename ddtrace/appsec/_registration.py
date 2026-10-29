"""AppSec's declarations for explicitly managed subscriber bundles."""

from ddtrace.internal.products import SubscriberBundle


WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace.appsec._contrib.web_framework.subscribers",
    subscribers=(
        "WebFrameworkRequestInspectSubscriber",
        "WebFrameworkResponseInspectSubscriber",
        "WebFrameworkBlockResponseSubscriber",
        "WebFrameworkRequestContextSubscriber",
    ),
)
