from contextlib import contextmanager

import attr
from perf import Scenario
from perf import Variant
from perf import run
from utils import gen_traces
from utils import init_encoder


@attr.s
class Encoder(Scenario):
    name = "encoder"
    spec = dict(ntraces=int, nspans=int, ntags=int, ltags=int, nmetrics=int)

    @contextmanager
    def run_ctx(self, variant):
        encoder = init_encoder()
        traces = gen_traces(**variant.spec)

        def _(loops):
            for _ in range(loops):
                encoder.encode_traces(traces)

        yield _


run(Encoder)
