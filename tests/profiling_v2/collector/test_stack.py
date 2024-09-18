import pytest


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_output_pprof"),
)
def test_output_pprof():
    import time

    from ddtrace import tracer
    from ddtrace.profiling import profiler

    p = profiler.Profiler(tracer=tracer)
    p.start()

    with tracer.trace("test"):
        time.sleep(1)

    p.stop()
