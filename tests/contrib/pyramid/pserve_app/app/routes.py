from pyramid.response import Response


def hello_world(request):
    return Response("Hello World!")


def includeme(config):
    config.add_route("hello", "/")
    config.add_view(hello_world, route_name="hello")
