
import ddtrace
from ddtrace.ext import http

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class TraceMiddleware(object):

    def __init__(self, app, tracer=None):
        self.app = app
        self.tracer = tracer or ddtrace.tracer


    async def __call__(self, scope, receive, send) -> None:
        with self.tracer.trace("responder.request", service="responder") as span:
            traced_send = _trace_asgi_send_func(send, span)
            span.set_tag(http.METHOD, scope.get('method'))
            await self.app(scope, receive, traced_send)

def _trace_asgi_send_func(send, span):

    def _traced_send(event):
        event_type = event.get("type")
        if event_type == "http.response.start":
            span.set_tag(http.STATUS_CODE, event.get("status"))
        return send(event)

    return _traced_send
