import pyperf

from ddtrace import tracer
from ddtrace.filters import TraceFilter


VARIANTS = [{}]


def time_trace(loops, variant):
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        with tracer.trace("1"):
            with tracer.trace("2"):
                with tracer.trace("3"):
                    with tracer.trace("4"):
                        with tracer.trace("5"):
                            pass
                        with tracer.trace("6"):
                            pass
                    with tracer.trace("7"):
                        pass
                with tracer.trace("8"):
                    pass
            with tracer.trace("9"):
                pass
    dt = pyperf.perf_counter() - t0
    return dt


# append a filter to drop traces so that no traces are encoded and sent to the agent
class DropTraces(TraceFilter):
    def process_trace(self, trace):
        return


if __name__ == "__main__":
    runner = pyperf.Runner()
    tracer.configure(settings={"FILTERS": [DropTraces()]})
    for variant in VARIANTS:
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        runner.bench_time_func("scenario:tracer|" + name, time_trace, variant)
