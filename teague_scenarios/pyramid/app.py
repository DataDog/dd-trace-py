#!/usr/bin/env python
from wsgiref.simple_server import make_server

from pyramid.config import Configurator
from pyramid.response import Response

def hello_world(request):
    return Response("Hello World!")


def tracer_shutdown(request):
    tracer.shutdown()
    return Response("shutdown")


if __name__ == "__main__":
    with Configurator() as config:
        config.add_route("hello_world", "/")
        config.add_view(hello_world, route_name="hello_world")
        app = config.make_wsgi_app()
    server = make_server("0.0.0.0", 5000, app)
    server.serve_forever()
