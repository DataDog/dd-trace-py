from ddtrace.appsec._contrib.web_framework.subscribers import WebFrameworkRequestContextSubscriber


class BottleRequestContextSubscriber(WebFrameworkRequestContextSubscriber):
    auto_register = False
    event_names = ("bottle.request",)
