from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from ddtrace.contrib.starlette import TraceMiddleware

app = Starlette()

async def homepage(request):
    response = "Success"
    return PlainTextResponse(response)

async def success(request):
    response = "Success"
    return PlainTextResponse(response)

async def error(request):
    """
    An example error. Switch the `debug` setting to see either tracebacks or 500 pages.
    """
    raise RuntimeError("Server error")

'''
async def not_found(request, exc):
    """
    Return an HTTP 404 page.
    """
    response = "Not Found"
    return PlainTextResponse(response)
'''

async def server_error(request, exc):
    """
    Return an HTTP 500 page.
    """
    response = "Server error"
    return PlainTextResponse(response)

def get_app(tracer):
    # add resource routing

    routes = [
        Route("/", endpoint=homepage, name="homepage", methods=["GET"]),
        Route("/200", endpoint=success, name="200", methods=["GET"]),
        Route("/500", endpoint=error, name="500", methods=["GET"])
    ]

    app = Starlette(routes=routes)

    return app