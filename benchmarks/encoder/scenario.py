import random
import string

import pyperf

from ddtrace.internal.encoding import Encoder
from ddtrace.span import Span


try:
    # the introduction of the buffered encoder changed the internal api
    # see https://github.com/DataDog/dd-trace-py/pull/2422
    from ddtrace.internal._encoding import BufferedEncoder  # noqa: F401

    def _init_encoder(max_size=8 << 20, max_item_size=8 << 20):
        return Encoder(max_size, max_item_size)


except ImportError:

    def _init_encoder():
        return Encoder()


VARIANTS = [
    dict(ntraces=1, nspans=1000),
    dict(ntraces=100, nspans=1000),
    dict(ntraces=1, nspans=1000, ntags=1, ltags=16),
    dict(ntraces=1, nspans=1000, ntags=48, ltags=64),
    dict(ntraces=1, nspans=1000, nmetrics=1),
    dict(ntraces=1, nspans=1000, nmetrics=48),
]


def _rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


def _random_values(k, size):
    if k == 0:
        return []

    return list(set([_rands(size=size) for _ in range(k)]))


def _gen_data(ntraces=1, nspans=1, ntags=0, ltags=0, nmetrics=0):
    traces = []

    # choose from a set of randomly generated span attributes
    span_names = _random_values(256, 16)
    resources = _random_values(256, 16)
    services = _random_values(16, 16)
    tag_keys = _random_values(ntags, 16)
    metric_keys = _random_values(nmetrics, 16)

    for _ in range(ntraces):
        trace = []
        for i in range(0, nspans):
            # first span is root so has no parent otherwise parent is root span
            parent_id = trace[0].span_id if i > 0 else None
            span_name = random.choice(span_names)
            resource = random.choice(resources)
            service = random.choice(services)
            with Span(None, span_name, resource=resource, service=service, parent_id=parent_id) as span:
                if ntags > 0:
                    span.set_tags(dict(zip(tag_keys, [_rands(size=ltags) for _ in range(ntags)])))
                if nmetrics > 0:
                    span.set_metrics(
                        dict(
                            zip(
                                metric_keys,
                                [random.randint(0, 2 ** 16) for _ in range(nmetrics)],
                            )
                        )
                    )
                trace.append(span)
        traces.append(trace)
    return traces


def time_encode(loops, variant):
    encoder = _init_encoder()
    traces = _gen_data(**variant)
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        encoder.encode_traces(traces)
    dt = pyperf.perf_counter() - t0
    return dt


if __name__ == "__main__":
    runner = pyperf.Runner()
    for variant in VARIANTS:
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        runner.bench_time_func("scenario:encoder|" + name, time_encode, variant)
