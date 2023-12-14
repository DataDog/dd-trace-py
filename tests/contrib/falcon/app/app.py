import falcon

from ddtrace.contrib.falcon import TraceMiddleware
from ddtrace.contrib.falcon.patch import FALCON_VERSION

from . import resources


def get_app(tracer=None, distributed_tracing=None):
    # initialize a traced Falcon application
    middleware = [TraceMiddleware(tracer, distributed_tracing=distributed_tracing)] if tracer else []

    if FALCON_VERSION >= (3, 0, 0):
        app = falcon.App(middleware=middleware)
    else:
        app = falcon.API(middleware=middleware)

    # add resource routing
    app.add_route("/200", resources.Resource200())
    app.add_route("/201", resources.Resource201())
    app.add_route("/500", resources.Resource500())
    app.add_route("/hello/{name}", resources.DynamicURIResource())
    app.add_route("/exception", resources.ResourceException())
    app.add_route("/not_found", resources.ResourceNotFound())
    return app
