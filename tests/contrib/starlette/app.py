from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse, FileResponse
from starlette.routing import Route
import os

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
    file_ptr = open("file.txt", "w+")
    file_ptr.write("Datadog is the best!")
    file_ptr.close()
    response = FileResponse("file.txt")
    return response

def file_clean_up():
    os.remove("file.txt")


def get_app(tracer):
    # add resource routing

    routes = [
        Route("/", endpoint=homepage, name="homepage", methods=["GET"]),
        Route("/200", endpoint=success, name="200", methods=["GET"]),
        Route("/500", endpoint=error, name="500", methods=["GET"]),
        Route("/stream", endpoint=stream, name="stream", methods=["GET"]),
        Route("/file", endpoint=file, name="file", methods=["GET"])
    ]

    app = Starlette(routes=routes)

    return app
