from starlette.applications import Starlette
from starlette.responses import Response, PlainTextResponse, StreamingResponse, FileResponse
from starlette.routing import Route
from tempfile import NamedTemporaryFile
import time


async def homepage(request):
    if "sleep" in request.query_params and request.query_params["sleep"]:
        time.sleep(3)
    response = "Success"
    return PlainTextResponse(response)


async def success(request):
    response = "Success"
    return PlainTextResponse(response)


async def create(request):
    response = "Created"
    return Response(response, status_code=201, headers=None, media_type=None)


async def error(request):
    """
    An example error. Switch the `debug` setting to see either tracebacks or 500 pages.
    """
    raise RuntimeError("Server error")


async def server_error(request, exc):
    """
    Return an HTTP 500 page.
    """
    response = "Server error"
    return PlainTextResponse(response)


def stream_response():
    yield b"streaming"


async def stream(request):
    return StreamingResponse(stream_response())


async def file(request):
    with NamedTemporaryFile(delete=False) as fp:
        fp.write(b"Datadog says hello!")
        fp.flush()
        return FileResponse(fp.name)


def get_app():
    routes = [
        Route("/", endpoint=homepage, name="homepage", methods=["GET"]),
        Route("/200", endpoint=success, name="200", methods=["GET"]),
        Route("/201", endpoint=create, name="201", methods=["POST"]),
        Route("/500", endpoint=error, name="500", methods=["GET"]),
        Route("/stream", endpoint=stream, name="stream", methods=["GET"]),
        Route("/file", endpoint=file, name="file", methods=["GET"]),
        Route("/users/{userid:int}", endpoint=success, name="path_params", methods=["GET"]),
        Route("/users/{userid:int}/info", endpoint=success, name="multi_path_params", methods=["GET"]),
        Route("/users/{userid:int}/{attribute:str}", endpoint=success, name="multi_path_params", methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    return app
