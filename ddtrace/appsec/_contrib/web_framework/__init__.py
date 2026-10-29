from ddtrace.appsec._registration import WEB_FRAMEWORK_SUBSCRIBERS


def listen() -> None:
    WEB_FRAMEWORK_SUBSCRIBERS.register()
