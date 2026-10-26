from ddtrace.appsec._contrib.bottle.subscribers import BottleRequestContextSubscriber


def listen() -> None:
    BottleRequestContextSubscriber.register()
