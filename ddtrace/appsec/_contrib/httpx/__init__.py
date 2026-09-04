from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxRequestContextSubscriber
from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxSingleRequestContextSubscriber


def listen() -> None:
    AppSecHttpxRequestContextSubscriber.register()
    AppSecHttpxSingleRequestContextSubscriber.register()


def unlisten() -> None:
    AppSecHttpxRequestContextSubscriber.unregister()
    AppSecHttpxSingleRequestContextSubscriber.unregister()
