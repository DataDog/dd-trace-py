from aiohttp.tracing import TraceConfig
from ddtrace import tracer
from ddtrace.propagation.http import HTTPPropagator


async def _on_request_start(session, trace_config_ctx, params):
    # TODO: other methods
    span = tracer.trace("ClientSession.{}".format(params.method))
    trace_config_ctx.span = span
    HTTPPropagator.inject(span.context, params.headers)


async def _on_request_end(session, trace_config_ctx, params):
    trace_config_ctx.span.finish()


class DataDog(TraceConfig):
    def __init__(self):
        super().__init__()

        self.on_request_start.append(_on_request_start)
        self.on_request_end.append(_on_request_end)
