import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount
from starlette.routing import Route

from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRequestPhase as Phase
from ddtrace.contrib.internal.asgi.lifecycle import HttpLifecycleMiddleware
from ddtrace.contrib.internal.starlette.patch import patch
from ddtrace.contrib.internal.starlette.patch import unpatch


@pytest.mark.asyncio
@pytest.mark.parametrize("method,status,handler_started", [("GET", 200, True), ("POST", 405, False)])
async def test_mounted_route_without_tracing(method, status, handler_started):
    from ddtrace.internal import core

    observed = []
    sent = []

    async def endpoint(request):
        return PlainTextResponse("ok")

    # Construct before patching so there is no TraceMiddleware or request span.
    app = Starlette(routes=[Mount("/api", routes=[Route("/items/{item}", endpoint)])])

    def observe(event):
        observed.append(event.snapshot)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    patch()
    core.on(HttpRequestDataEvent.event_name, observe)
    try:
        await HttpLifecycleMiddleware(app, framework="starlette")(
            {
                "type": "http",
                "method": method,
                "path": "/api/items/one",
                "root_path": "",
                "scheme": "http",
                "headers": [],
                "query_string": b"",
                "server": ("test", 80),
            },
            receive,
            send,
        )
    finally:
        core.reset_listeners(HttpRequestDataEvent.event_name, observe)
        unpatch()
    assert sent[0]["status"] == status
    routes = [s for s in observed if s.phase == Phase.ROUTE_RESOLVED]
    assert len(routes) == 1
    assert routes[0].request.route == "/api/items/{item}"
    assert observed[-1].handler_started is handler_started
    assert sum(s.phase == Phase.BEFORE_HANDLER for s in observed) == int(handler_started)
    assert all(s.framework == "starlette" and not s.supported_actions for s in observed)
