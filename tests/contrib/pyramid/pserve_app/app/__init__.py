from pyramid.config import Configurator
from pyramid.response import Response

from ddtrace import tracer
from ddtrace.filters import TraceFilter


class PingFilter(TraceFilter):
    def process_trace(self, trace):
        # Filter out all traces with trace_id = 1
        # This is done to prevent certain traces from being included in snapshots and
        # accomplished by propagating an http trace id of 1 with the request to the webserver.
        return None if trace and trace[0].trace_id == 1 else trace


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)


def tracer_shutdown(request):
    tracer.shutdown()
    return Response("shutdown")


def main(global_config, **settings):
    """This function returns a Pyramid WSGI application."""
    with Configurator(settings=settings) as config:
        config.add_route("tracer-shutdown", "/shutdown-tracer")
        config.add_view(tracer_shutdown, route_name="tracer-shutdown")
        # This will trigger usage of the package name reproing an issue
        # reported in #2942.
        config.include(".routes")
        config.scan()
    return config.make_wsgi_app()
