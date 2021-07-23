import pyperf
from util import gen_metrics
from util import gen_spans
from util import gen_tags

from ddtrace import Span


def time_start_span(loops, variant):
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        for _ in range(0, variant.get("nspans")):
            Span(None, "test.op", resource="resource", service="service", trace_id=variant.get("trace_id"))
    dt = pyperf.perf_counter() - t0
    return dt


def time_add_tags(loops, span, tags):
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        span.set_tags(tags)
    dt = pyperf.perf_counter() - t0
    return dt


def time_add_metrics(loops, span, metrics):
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        span.set_metrics(metrics)
    dt = pyperf.perf_counter() - t0
    return dt


def time_finish_span(loops, spans):
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        for span in spans:
            span.finish()
    dt = pyperf.perf_counter() - t0
    return dt


if __name__ == "__main__":
    runner = pyperf.Runner()
    runner.metadata["scenario"] = "span"
    for variant in [dict(nspans=1000), dict(nspans=1000, trace_id=1)]:
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        runner.bench_time_func("perf_group:span|perf_case:init_span|" + name, time_start_span, variant)

    for variant in [dict(ntags=500, ltags=100), dict(ntags=1000, ltags=300)]:
        span = Span(None, "test.op", resource="resource", service="service", trace_id=1)
        tags = gen_tags(**variant)
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        runner.bench_time_func("perf_group:span|perf_case:add_tags|" + name, time_add_tags, span, tags)

    for variant in [dict(nmetrics=500, lmetrics=100), dict(nmetrics=1000, lmetrics=300)]:
        span = Span(None, "test.op", resource="resource", service="service", trace_id=1)
        metrics = gen_metrics(**variant)
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        runner.bench_time_func("perf_group:span|perf_case:add_metrics|" + name, time_add_metrics, span, metrics)

    for variant in [dict(nspans=10000)]:
        gen_spans(**variant)
        spans = gen_spans(**variant)
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        runner.bench_time_func("perf_group:span|perf_case:finish_span|" + name, time_finish_span, spans)
