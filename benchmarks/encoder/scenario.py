import bm
import utils


class Encoder(bm.Scenario):
    ntraces: int
    nspans: int
    ntags: int
    ltags: int
    nmetrics: int
    encoding: str
    dd_origin: bool
    span_events: bool
    top_level_span_events: bool

    def run(self):
        encoder = utils.init_encoder(self.encoding)
        traces = utils.gen_traces(self)

        def _(loops):
            for _ in range(loops):
                for trace in traces:
                    encoder.put(trace)
                    encoder.encode()

        yield _
