import bm
import utils


try:
    from ddtrace.internal.encoding import MsgpackEncoderV05

    SUPPORTED_VERSIONS = frozenset(["v0.3", "v0.5"])
except ImportError:
    SUPPORTED_VERSIONS = frozenset(["v0.3"])


@bm.register
class Encoder(bm.Scenario):
    ntraces = bm.var(type=int)
    nspans = bm.var(type=int)
    ntags = bm.var(type=int)
    nnames = bm.var(type=int)
    nservices = bm.var(type=int)
    nresources = bm.var(type=int)
    ltags = bm.var(type=int)
    nmetrics = bm.var(type=int)
    version = bm.var(type=str)

    def run(self):
        if self.version not in SUPPORTED_VERSIONS:
            yield lambda _: None
            return

        encoder = utils.init_encoder(self.version)
        traces = utils.gen_traces(self)

        def _(loops):
            for _ in range(loops):
                for trace in traces:
                    encoder.put(trace)
                    encoder.encode()

        yield _
