from ddtrace.appsec._registration import registration


def listen() -> None:
    registration.register("web_framework")
