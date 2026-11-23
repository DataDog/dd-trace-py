import pathlib

from aiohttp import web
from aiohttp.abc import AbstractRouter
import pytest

from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config


async def handler(request):
    return web.Response(text="ok")


class ItemView(web.View):
    async def get(self):
        return web.Response(text="ok")

    async def post(self):
        return web.Response(text="ok")


@pytest.fixture(autouse=True)
def clean_endpoint_collection():
    endpoint_collection.reset()
    yield
    endpoint_collection.reset()


def _collected():
    return {(ep.method, ep.path, ep.resource_name, ep.operation_name) for ep in endpoint_collection.endpoints}


def _endpoint(method, path):
    return (method, path, f"{method} {path}", "aiohttp.request")


def _application():
    app = web.Application()
    app.router.add_get("/", handler)
    app.router.add_route("*", "/any", handler)
    app.router.add_post("/users/{id}", handler)
    app.router.add_get(r"/files/{name:\w+}.txt", handler)
    app.router.add_resource("/named/{x}", name="named").add_route("PUT", handler)
    app.router.add_static("/static", pathlib.Path(__file__).parent)
    app.router.add_view("/view", ItemView)
    return app


def _sub_application():
    sub = web.Application()
    sub.router.add_get("/inner/{y}", handler)
    nested = web.Application()
    nested.router.add_delete("/deep", handler)
    sub.add_subapp("/nested", nested)
    return sub


async def test_routes_are_collected_when_the_application_starts(aiohttp_client):
    app = _application()
    app.add_subapp("/sub", _sub_application())
    domain = web.Application()
    domain.router.add_get("/domain", handler)
    app.add_domain("example.com", domain)
    trace_app(app)
    assert _collected() == set()

    await aiohttp_client(app)

    assert _collected() == {
        _endpoint("GET", "/"),
        _endpoint("HEAD", "/"),
        _endpoint("*", "/any"),
        _endpoint("POST", "/users/{id}"),
        _endpoint("GET", "/files/{name}.txt"),
        _endpoint("HEAD", "/files/{name}.txt"),
        _endpoint("PUT", "/named/{x}"),
        _endpoint("GET", "/static"),
        _endpoint("HEAD", "/static"),
        _endpoint("GET", "/view"),
        _endpoint("POST", "/view"),
        _endpoint("GET", "/sub/inner/{y}"),
        _endpoint("HEAD", "/sub/inner/{y}"),
        _endpoint("DELETE", "/sub/nested/deep"),
        _endpoint("GET", "/domain"),
        _endpoint("HEAD", "/domain"),
    }


async def test_traced_sub_application_reports_its_mounted_paths(aiohttp_client):
    app = web.Application()
    app.router.add_get("/", handler)
    sub = _sub_application()
    trace_app(sub)
    app.add_subapp("/sub", sub)

    await aiohttp_client(app)

    assert _collected() == {
        _endpoint("GET", "/sub/inner/{y}"),
        _endpoint("HEAD", "/sub/inner/{y}"),
        _endpoint("DELETE", "/sub/nested/deep"),
    }


async def test_routes_are_not_collected_when_endpoint_collection_is_disabled(aiohttp_client, monkeypatch):
    monkeypatch.setattr(appsec_telemetry_config, "ENDPOINT_COLLECTION_ENABLED", False)
    app = _application()
    trace_app(app)

    await aiohttp_client(app)

    assert _collected() == set()


async def test_trace_app_called_from_a_startup_handler_collects_the_routes(aiohttp_client):
    app = _application()

    async def trace_on_startup(app):
        trace_app(app)

    app.on_startup.append(trace_on_startup)

    await aiohttp_client(app)

    assert _endpoint("POST", "/users/{id}") in _collected()


class _HandRolledRouter(AbstractRouter):
    async def resolve(self, request):
        raise web.HTTPNotFound()


def _hand_rolled_router_application():
    with pytest.warns(DeprecationWarning):
        return web.Application(router=_HandRolledRouter())


async def test_application_with_a_router_without_routes_starts(aiohttp_client):
    app = _hand_rolled_router_application()
    trace_app(app)

    client = await aiohttp_client(app)

    assert (await client.get("/")).status == 404
    assert _collected() == set()


async def test_trace_app_called_from_a_startup_handler_with_a_router_without_routes(aiohttp_client):
    app = _hand_rolled_router_application()

    async def trace_on_startup(app):
        trace_app(app)

    app.on_startup.append(trace_on_startup)

    await aiohttp_client(app)

    assert _collected() == set()


async def test_trace_app_called_from_a_cleanup_context_collects_the_routes(aiohttp_client):
    app = _application()

    async def trace_in_cleanup_context(app):
        trace_app(app)
        yield

    app.cleanup_ctx.append(trace_in_cleanup_context)

    await aiohttp_client(app)

    assert _endpoint("POST", "/users/{id}") in _collected()
