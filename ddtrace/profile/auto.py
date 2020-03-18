"""Automatically starts a collector when imported."""
from ddtrace.profile.bootstrap import sitecustomize  # noqa

start_profiler = sitecustomize.start_profiler
