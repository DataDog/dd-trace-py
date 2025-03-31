import bm
import utils

from ddtrace.settings._agent import config as agent_config


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
        if self.top_level_span_events and hasattr(agent_config, "trace_native_span_events"):
            agent_config.trace_native_span_events = True
        encoder = utils.init_encoder(self.encoding)
        traces = utils.gen_traces(self)

        def _(loops):
            for _ in range(loops):
                for trace in traces:
                    encoder.put(trace)
                    encoder.encode()

        yield _
