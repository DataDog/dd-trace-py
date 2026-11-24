"""AppSec-owned bundles shared by direct and remote activation."""

from ddtrace.internal.core.subscriber import SubscriberBundle
from ddtrace.internal.settings.asm import config


WEB_FRAMEWORK_SUBSCRIBERS = (
    SubscriberBundle(
        module="ddtrace.appsec._contrib.web_framework.subscribers",
        subscribers=("AppSecWebFrameworkSubscriber",),
    ),
)


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


def register() -> None:
    if config._asm_enabled:
        for bundle in WEB_FRAMEWORK_SUBSCRIBERS:
            bundle.register()
        if config._ep_enabled:
            for bundle in EXPLOIT_PREVENTION_SUBSCRIBERS:
                bundle.register()


def unregister() -> None:
    # These listeners inspect operations; request cleanup is owned by asm_listen().
    for bundle in WEB_FRAMEWORK_SUBSCRIBERS + EXPLOIT_PREVENTION_SUBSCRIBERS:
        bundle.unregister()
