"""AppSec-owned bundles shared by direct and remote activation."""

from ddtrace.internal.core.subscriber import SubscriberBundle
from ddtrace.internal.settings.asm import config


EXPLOIT_PREVENTION_SUBSCRIBERS = (
    SubscriberBundle(
        module="ddtrace.appsec._contrib.dbapi.subscribers",
        subscribers=("AppSecDbApiSubscriber",),
    ),
    SubscriberBundle(
        module="ddtrace.appsec._contrib.httpx.subscribers",
        subscribers=("AppSecHttpxRequestContextSubscriber", "AppSecHttpxSingleRequestContextSubscriber"),
    ),
    SubscriberBundle(
        module="ddtrace.appsec._contrib.subprocess.subscribers",
        subscribers=("AppSecSubprocessCommandSubscriber",),
    ),
)


WEB_FRAMEWORK_REQUEST_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace.appsec._contrib.web_framework.subscribers",
    subscribers=("WebFrameworkRequestInspectSubscriber",),
)
WEB_FRAMEWORK_COMPLETION_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace.appsec._contrib.web_framework.subscribers",
    subscribers=(
        "WebFrameworkResponseInspectSubscriber",
        "WebFrameworkBlockResponseSubscriber",
        "WebFrameworkRequestContextSubscriber",
    ),
)


def register() -> None:
    if config._asm_enabled:
        WEB_FRAMEWORK_REQUEST_SUBSCRIBERS.register()
        WEB_FRAMEWORK_COMPLETION_SUBSCRIBERS.register()
    if config._asm_enabled and config._ep_enabled:
        for bundle in EXPLOIT_PREVENTION_SUBSCRIBERS:
            bundle.register()


def unregister() -> None:
    WEB_FRAMEWORK_REQUEST_SUBSCRIBERS.unregister()
    # Retain response replacement and cleanup for requests that outlive product stop.
    # These listeners inspect operations; request cleanup is owned by asm_listen().
    for bundle in EXPLOIT_PREVENTION_SUBSCRIBERS:
        bundle.unregister()
