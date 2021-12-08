from wsgiref.simple_server import make_server

from pyramid.config import Configurator
from pyramid.response import Response

from ddtrace import tracer
from tests.webclient import PingFilter


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)


def hello_world(request):
    return Response("Hello World!")


def tracer_shutdown(request):
    tracer.shutdown()
    return Response("shutdown")


if __name__ == "__main__":
    with Configurator() as config:
        config.add_route("hello", "/")
        config.add_view(hello_world, route_name="hello")

        config.add_route("tracer-shutdown", "/shutdown-tracer")
        config.add_view(tracer_shutdown, route_name="tracer-shutdown")

        app = config.make_wsgi_app()
    server = make_server("0.0.0.0", 8000, app)
    server.serve_forever()
