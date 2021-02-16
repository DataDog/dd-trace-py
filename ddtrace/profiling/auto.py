"""Automatically starts a collector when imported."""
from ddtrace.profiling.bootstrap import sitecustomize  # noqa


start_profiler = sitecustomize.start_profiler
