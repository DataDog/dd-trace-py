from ddtrace.appsec._contrib.requests.subscribers import AppSecRequestsSubscriber


def listen() -> None:
    if AppSecRequestsSubscriber.activation is None:
        AppSecRequestsSubscriber.activation = object()
    AppSecRequestsSubscriber.register()


def unlisten() -> None:
    AppSecRequestsSubscriber.activation = None
    AppSecRequestsSubscriber.unregister()
