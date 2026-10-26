from ddtrace.appsec._contrib.web_framework.subscribers import WebFrameworkBlockResponseSubscriber
from ddtrace.appsec._contrib.web_framework.subscribers import WebFrameworkRequestContextSubscriber
from ddtrace.appsec._contrib.web_framework.subscribers import WebFrameworkRequestInspectSubscriber
from ddtrace.appsec._contrib.web_framework.subscribers import WebFrameworkResponseInspectSubscriber


def listen() -> None:
    WebFrameworkRequestInspectSubscriber.register()
    WebFrameworkResponseInspectSubscriber.register()
    WebFrameworkBlockResponseSubscriber.register()
    WebFrameworkRequestContextSubscriber.register()
