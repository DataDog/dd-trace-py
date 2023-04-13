import bm
import bm.utils as utils

from ddtrace import config


class Span(bm.Scenario):
    nspans = bm.var(type=int)
    ntags = bm.var(type=int)
    ltags = bm.var(type=int)
    nmetrics = bm.var(type=int)
    finishspan = bm.var_bool()
    traceid128 = bm.var_bool()

    def run(self):
        # run scenario to also set tags on spans
        tags = utils.gen_tags(self)
        settags = len(tags) > 0

        # run scenario to also set metrics on spans
        metrics = utils.gen_metrics(self)
        setmetrics = len(metrics) > 0

        # run scenario to include finishing spans
        finishspan = self.finishspan
        config._128_bit_trace_id_enabled = self.traceid128

        def _(loops):
            for _ in range(loops):
                for i in range(self.nspans):
                    s = utils.gen_span("test." + str(i))
                    if settags:
                        s.set_tags(tags)
                    if setmetrics:
                        s.set_metrics(metrics)
                    if finishspan:
                        s.finish()

        yield _
