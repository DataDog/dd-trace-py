"""Real-framework checks for web request metadata and tracing admission."""

import json


COMMON = """
from ddtrace.trace import tracer
from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
from ddtrace.internal import core
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.standalone import standalone_config

asm_config._asm_enabled = mode == "standalone"
standalone_config.apm_tracing_enabled = mode != "standalone"
tracer.enabled = mode != "disabled" and mode != "standalone"
if mode in ("unregister", "standalone"):
    WEB_FRAMEWORK_SUBSCRIBERS.unregister()

ended = []
started = []
spans = []
core.on("context.started.web.request", lambda ctx: started.append(ctx))
def observe(ctx, exc_info):
    ended.append((ctx.event.response_status_code, ctx.event.span_creation_enabled))
core.on("context.ended.web.request", observe)

def check_request():
    assert started
    ctx = started[-1]
    span = ctx.get_item("_inner_span")
    assert (span is not None) == (mode in ("standalone", "enabled")), span
    if span is not None:
        assert span.span_type == "web"
        spans.append(span)

def wsgi_request(app):
    from wsgiref.util import setup_testing_defaults
    environ = {}
    setup_testing_defaults(environ)
    status = []
    def start_response(value, headers, exc_info=None):
        status.append(value)
    result = app(environ, start_response)
    try:
        b"".join(result)
    finally:
        if hasattr(result, "close"):
            result.close()
    assert status and status[0].startswith("200"), status
"""


APPLICATIONS = {
    "aiohttp": """
import asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
async def main():
    async def handler(request):
        check_request()
        return web.Response(text="ok")
    app = web.Application()
    app.router.add_get("/", handler)
    trace_app(app)
    async with TestClient(TestServer(app)) as client:
        for _ in range(2):
            response = await client.get("/")
            assert response.status == 200
            await response.read()
asyncio.run(main())
""",
    "bottle": """
import bottle
from ddtrace.contrib.internal.bottle.trace import TracePlugin
app = bottle.Bottle()
app.install(TracePlugin())
def handler():
    check_request()
    return "ok"
app.route("/", callback=handler)
for _ in range(2):
    wsgi_request(app)
""",
    "cherrypy": """
import cherrypy
from ddtrace.contrib.internal.cherrypy.patch import TraceMiddleware
class Root:
    @cherrypy.expose
    def index(self):
        check_request()
        return "ok"
app = cherrypy.Application(Root(), config={"/": {"tools.tracer.on": True}})
TraceMiddleware(cherrypy)
for _ in range(2):
    wsgi_request(app)
""",
    "falcon": """
import falcon
from falcon import testing
from ddtrace.contrib.internal.falcon.middleware import TraceMiddleware
class Resource:
    def on_get(self, req, resp):
        check_request()
        resp.data = b"ok"
application_class = getattr(falcon, "App", None) or falcon.API
app = application_class(middleware=[TraceMiddleware()])
app.add_route("/", Resource())
client = testing.TestClient(app)
for _ in range(2):
    assert client.simulate_get("/").status_code == 200
""",
    "molten": """
import molten
from molten.testing import TestClient
from ddtrace.contrib.internal.molten.patch import patch
patch()
def handler() -> str:
    check_request()
    return "ok"
app = molten.App(routes=[molten.Route("/", handler)])
client = TestClient(app)
for _ in range(2):
    assert client.request("GET", "/").status_code == 200
""",
    "pyramid": """
from pyramid.config import Configurator
from pyramid.response import Response
from webob import Request
def handler(request):
    check_request()
    return Response("ok")
with Configurator() as configurator:
    configurator.include("ddtrace.contrib.pyramid")
    configurator.add_route("home", "/")
    configurator.add_view(handler, route_name="home")
    app = configurator.make_wsgi_app()
for _ in range(2):
    assert Request.blank("/").get_response(app).status_code == 200
""",
    "sanic": """
import asyncio
import sanic
from sanic.response import text
from ddtrace.contrib.internal.sanic.patch import patch
patch()
app = sanic.Sanic("web_lifecycle")
@app.get("/")
async def handler(request):
    check_request()
    return text("ok")
async def main():
    for _ in range(2):
        _, response = await app.asgi_client.get("/")
        assert response.status == 200
if tuple(int(part) for part in sanic.__version__.split(".")[:2]) < (21, 9):
    # Older Sanic bundles its synchronous client; sanic-testing is not installed.
    for _ in range(2):
        _, response = app.test_client.get("/")
        assert response.status == 200
else:
    asyncio.run(main())
""",
}


def assert_web_lifecycle(run_python_code_in_subprocess, framework, mode):
    code = f"mode = {mode!r}\n" + COMMON + APPLICATIONS[framework]
    code += """
assert ended == [(200, mode in ("standalone", "enabled"))] * 2, ended
assert len(spans) == (2 if mode in ("standalone", "enabled") else 0), spans
assert all(span.finished for span in spans)
assert all(span.get_tag("http.status_code") == "200" for span in spans)
import json
print(json.dumps({
    "mode": mode,
    "requests": ended,
    "spans": [{"name": span.name, "resource": span.resource, "span_type": span.span_type,
               "trace_id": str(span.trace_id), "span_id": str(span.span_id), "parent_id": span.parent_id,
               "error": span.error, "finished": span.finished, "tags": span.get_tags(),
               "metrics": span.get_metrics()} for span in spans],
}))
"""
    out, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, (out.decode(), err.decode())
    return json.loads(out.decode().splitlines()[-1])
