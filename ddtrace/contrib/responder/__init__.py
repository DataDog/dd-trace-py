
import ddtrace
from ddtrace.ext import http
from ddtrace.propagation.http import extract_context_from_http_headers

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class TraceMiddleware(object):

    def __init__(self, app, tracer=None):
        self.app = app
        self.tracer = tracer or ddtrace.tracer


    async def __call__(self, scope, receive, send) -> None:

        headers = {k.decode():v for k,v in scope.get('headers', ())}
        ctx = extract_context_from_http_headers(headers)
        if ctx.trace_id:
            self.tracer.context_provider.activate(ctx)

        with self.tracer.trace("responder.request", service="responder") as span:
            traced_send = _trace_asgi_send_func(send, span)
            span.set_tag(http.METHOD, scope.get('method'))
            await self.app(scope, receive, traced_send)

def _trace_asgi_send_func(send, span):

    async def _traced_send(event):
        event_type = event.get("type")
        if event_type == "http.response.start":
            span.set_tag(http.STATUS_CODE, event.get("status"))
        await send(event)

    return _traced_send
