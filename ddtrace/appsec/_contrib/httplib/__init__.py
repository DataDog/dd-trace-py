from ddtrace.appsec._contrib.httplib.subscribers import AppSecHttpClientRequestSubscriber
from ddtrace.appsec._contrib.httplib.subscribers import AppSecHttpClientResponseSubscriber
from ddtrace.contrib.internal.httplib.events import patch_http_client_events
from ddtrace.contrib.internal.httplib.events import unpatch_http_client_events


def listen() -> None:
    patch_http_client_events()
    AppSecHttpClientRequestSubscriber.register()
    AppSecHttpClientResponseSubscriber.register()


def unlisten() -> None:
    AppSecHttpClientRequestSubscriber.unregister()
    AppSecHttpClientResponseSubscriber.unregister()
    unpatch_http_client_events()
