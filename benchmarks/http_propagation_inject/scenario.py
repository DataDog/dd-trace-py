import json

import bm

from ddtrace.context import Context
from ddtrace.propagation import http


class HTTPPropagationInject(bm.Scenario):
    sampling_priority = bm.var(type=str)
    dd_origin = bm.var(type=str)
    meta = bm.var(type=str)

    def run(self):
        sampling_priority = None
        if self.sampling_priority != "":
            sampling_priority = int(self.sampling_priority)
        dd_origin = self.dd_origin or None

        meta = None
        if self.meta:
            meta = json.loads(self.meta)

        ctx = Context(
            trace_id=8336172473188639332,
            span_id=6804240797025004118,
            sampling_priority=sampling_priority,
            dd_origin=dd_origin,
            meta=meta,
        )

        def _(loops):
            for _ in range(loops):
                # Just pass in a new/empty dict, we don't care about the result
                http.HTTPPropagator.inject(ctx, {})

        yield _
