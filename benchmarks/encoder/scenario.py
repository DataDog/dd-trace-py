from contextlib import contextmanager

import attr
import bm
import utils



class Encoder(bm.Scenario):
    @attr.s
    class Config(bm.Scenario.Config):
        ntraces = attr.ib(type=int)
        nspans = attr.ib(type=int)
        ntags = attr.ib(type=int)
        ltags = attr.ib(type=int)
        nmetrics = attr.ib(type=int)

    @contextmanager
    def run_ctx(self):
        encoder = utils.init_encoder()
        traces = utils.gen_traces(self.config)

        def _(loops):
            for _ in range(loops):
                encoder.encode_traces(traces)

        yield _


bm.register(Encoder)
