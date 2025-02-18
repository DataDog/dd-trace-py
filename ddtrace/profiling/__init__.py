from ddtrace.internal.module import lazy


@lazy
def _():
    from ddtrace.profiling.profiler import Profiler  # noqa:F401
