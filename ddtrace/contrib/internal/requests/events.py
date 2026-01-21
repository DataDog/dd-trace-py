from ddtrace.contrib.events.http_client import HttpClientRequestEvent
from ddtrace.internal import core


class RequestsHttpClientRequestEvent(HttpClientRequestEvent):
    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        print("Hello, This is request")
