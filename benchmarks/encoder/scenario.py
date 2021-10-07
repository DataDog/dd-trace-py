import bm
import utils


class Encoder(bm.Scenario):
    ntraces = bm.var(type=int)
    nspans = bm.var(type=int)
    ntags = bm.var(type=int)
    ltags = bm.var(type=int)
    nmetrics = bm.var(type=int)
    dd_origin = bm.var(type=bool)

    def init(self):
        utils.gen_data()

    def run(self):
        encoder = utils.init_encoder()
        traces = utils.load_traces(self.__dict__)

        def _():
            for trace in traces:
                encoder.put(trace)
                encoder.encode()

        yield _
