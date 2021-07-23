import pyperf

from ddtrace import tracer


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


# use the fake processor so that spans are not written
class FakeTraceProcessor:
    def process_trace(self, trace):
        return


if __name__ == "__main__":
    runner = pyperf.Runner()
    fake_processor = FakeTraceProcessor()
    # append fake processor to the end of processors so that all processors run before traces are tossed out
    tracer._span_processors[0]._trace_processors.append(fake_processor)
    for variant in VARIANTS:
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        runner.bench_time_func("scenario:tracer|" + name, time_trace, variant)
