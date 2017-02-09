import os

from aiohttp import web


BASE_DIR = os.path.dirname(os.path.realpath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'statics')


async def home(request):
    return web.Response(text="What's tracing?")


async def name(request):
    name = request.match_info.get('name', 'Anonymous')
    return web.Response(text='Hello {}'.format(name))


async def coroutine_chaining(request):
    tracer = get_tracer(request)
    span = tracer.trace('aiohttp.coro_1')
    text = await coro_2(request)
    span.finish()
    return web.Response(text=text)


async def coro_2(request):
    tracer = get_tracer(request)
    with tracer.trace('aiohttp.coro_2') as span:
        span.set_tag('aiohttp.worker', 'pending')
    return 'OK'


def setup_app(loop):
    """
    Use this method to create the app. It must receive
    the ``loop`` provided by the ``get_app`` method of
    ``AioHTTPTestCase`` class.
    """
    # configure the app
    app = web.Application(loop=loop)
    app.router.add_get('/', home)
    app.router.add_get('/echo/{name}', name)
    app.router.add_get('/chaining/', coroutine_chaining)
    app.router.add_static('/statics', STATIC_DIR)
    return app


def get_tracer(request):
    """
    Utility function to retrieve the tracer from the given ``request``.
    It is meant to be used only for testing purposes.
    """
    return request['__datadog_request_span']._tracer
