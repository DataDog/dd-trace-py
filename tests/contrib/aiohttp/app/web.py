import asyncio
import os

from aiohttp import web


try:
    import aiohttp_jinja2
except ImportError:
    aiohttp_jinja2 = None
else:
    import jinja2

from ddtrace.contrib.aiohttp.middlewares import CONFIG_KEY


BASE_DIR = os.path.dirname(os.path.realpath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "statics")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")


async def home(request):
    return web.Response(text="What's tracing?")


async def name(request):
    name = request.match_info.get("name", "Anonymous")
    return web.Response(text="Hello {}".format(name))


async def response_headers(request):
    response = web.Response(text="response_headers_test")
    response.headers["my-response-header"] = "my_response_value"
    return response


async def coroutine_chaining(request):
    tracer = get_tracer(request)
    span = tracer.trace("aiohttp.coro_1")
    text = await coro_2(request)
    span.finish()
    return web.Response(text=text)


def route_exception(request):
    raise Exception("error")


async def route_async_exception(request):
    raise Exception("error")


async def route_wrapped_coroutine(request):
    tracer = get_tracer(request)

    @tracer.wrap("nested")
    async def nested():
        await asyncio.sleep(0.25)

    await nested()
    return web.Response(text="OK")


async def route_sub_span(request):
    tracer = get_tracer(request)
    with tracer.trace("aiohttp.sub_span") as span:
        span.set_tag("sub_span", "true")
        return web.Response(text="OK")


async def uncaught_server_error(request):
    return 1 / 0


async def caught_server_error(request):
    return web.Response(text="NOT OK", status=503)


async def coro_2(request):
    tracer = get_tracer(request)
    with tracer.trace("aiohttp.coro_2") as span:
        span.set_tag("aiohttp.worker", "pending")
    return "OK"


async def delayed_handler(request):
    await asyncio.sleep(0.01)
    return web.Response(text="Done")


async def stream_handler(request):
    async def async_range(count):
        for i in range(count):
            yield i

    response = web.StreamResponse()
    await response.prepare(request)
    await asyncio.sleep(0.5)
    async for i in async_range(10):
        await response.write(f"{i}".encode())
    return response


async def noop_middleware(app, handler):
    async def middleware_handler(request):
        # noop middleware
        response = await handler(request)
        return response

    return middleware_handler


if aiohttp_jinja2:

    async def template_handler(request):
        return aiohttp_jinja2.render_template("template.jinja2", request, {"text": "OK"})

    @aiohttp_jinja2.template("template.jinja2")
    async def template_decorator(request):
        return {"text": "OK"}

    @aiohttp_jinja2.template("error.jinja2")
    async def template_error(request):
        return {}


def setup_app(loop=None):
    """
    Use this method to create the app. It must receive
    the ``loop`` provided by the ``get_app`` method of
    ``AioHTTPTestCase`` class.
    """
    # configure the app
    app = web.Application(
        loop=loop,
        middlewares=[
            noop_middleware,
        ],
    )
    app.router.add_get("/", home)
    app.router.add_get("/delayed/", delayed_handler)
    app.router.add_get("/stream/", stream_handler)
    app.router.add_get("/echo/{name}", name)
    app.router.add_get("/chaining/", coroutine_chaining)
    app.router.add_get("/exception", route_exception)
    app.router.add_get("/async_exception", route_async_exception)
    app.router.add_get("/wrapped_coroutine", route_wrapped_coroutine)
    app.router.add_get("/sub_span", route_sub_span)
    app.router.add_get("/uncaught_server_error", uncaught_server_error)
    app.router.add_get("/caught_server_error", caught_server_error)
    app.router.add_static("/statics", STATIC_DIR)
    if aiohttp_jinja2:
        # configure templates
        set_memory_loader(app)
        app.router.add_get("/template/", template_handler)
        app.router.add_get("/template_decorator/", template_decorator)
        app.router.add_get("/template_error/", template_error)
    app.router.add_get("/response_headers/", response_headers)

    return app


def set_memory_loader(app):
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.DictLoader(
            {
                "template.jinja2": "{{text}}",
                "error.jinja2": "{{1/0}}",
            }
        ),
    )


def set_filesystem_loader(app):
    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(TEMPLATE_DIR))


def set_package_loader(app):
    aiohttp_jinja2.setup(app, loader=jinja2.PackageLoader("tests.contrib.aiohttp.app", "templates"))


def get_tracer(request):
    """
    Utility function to retrieve the tracer from the given ``request``.
    It is meant to be used only for testing purposes.
    """
    return request.app[CONFIG_KEY]["tracer"]
