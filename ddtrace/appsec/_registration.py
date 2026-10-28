"""AppSec's declarations for explicitly managed subscriber bundles."""

from ddtrace.internal.product_registration import ProductRegistration
from ddtrace.internal.product_registration import SubscriberBundle


registration = ProductRegistration(
    name="appsec",
    subscriber_bundles={
        "web_framework": SubscriberBundle(
            module="ddtrace.appsec._contrib.web_framework.subscribers",
            subscribers=(
                "WebFrameworkRequestInspectSubscriber",
                "WebFrameworkResponseInspectSubscriber",
                "WebFrameworkBlockResponseSubscriber",
                "WebFrameworkRequestContextSubscriber",
            ),
        ),
    },
)
