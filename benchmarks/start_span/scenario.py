import os

import pyperf
from util import gen_traces

from ddtrace.internal.encoding import Encoder


try:
    from ddtrace.internal._encoding import BufferedEncoder

    def init_encoder(max_size=8 << 20, max_item_size=8 << 20):
        return Encoder(max_size, max_item_size)


except:

    def init_encoder():
        return Encoder()


VARIANTS = [
    dict(ntraces=1, nspans=1000),
    dict(ntraces=100, nspans=1000),
    dict(ntraces=1, nspans=1000, ntags=1, ltags=16),
    dict(ntraces=1, nspans=1000, ntags=100, ltags=128),
    dict(ntraces=1, nspans=1000, nmetrics=1),
    dict(ntraces=1, nspans=1000, nmetrics=100),
]


def time_encode(loops, encoder, traces):
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        encoder.encode_traces(traces)
    dt = pyperf.perf_counter() - t0
    return dt


if __name__ == "__main__":
    runner = pyperf.Runner()
    runner.metadata["scenario"] = "encoder"
    for variant in VARIANTS:
        encoder = init_encoder()
        traces = gen_traces(**variant)
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        metadata = {}
        runner.bench_time_func(name, time_encode, encoder, traces, metadata=metadata)
