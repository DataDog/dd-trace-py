import bm

from ddtrace.context import Context
from ddtrace.propagation import http


class HTTPPropagationInject(bm.Scenario):
    def run():
        ctx = Context()

        def _(loops):
            for _ in range(loops):
                # Just pass in a new/empty dict, we don't care about the result
                http.HTTPPropagator.inject(ctx, {})

        yield _
