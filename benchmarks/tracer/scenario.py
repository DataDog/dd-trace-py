import pyperf

from ddtrace import tracer
# should I have the traced variant be True or False? Or should I just directly pass in the function via the variant?
VARIANTS = [
    dict(traced=True, calls=100),
    dict(traced=False, calls=100),
]

def traced_function():
    with tracer.trace("trace.function"):
        return

def untraced_function():
    return


def time_trace(loops, variant):
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        if variant.get("traced") == True:
            for _ in range(variant.get("calls")):
                traced_function()
        else: 
            for _ in range(variant.get("calls")):
                untraced_function()
    dt = pyperf.perf_counter() - t0
    return dt


if __name__ == "__main__":
    runner = pyperf.Runner()
    for variant in VARIANTS:
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        runner.bench_time_func("scenario:tracer|" + name, time_trace, variant)
