"""Proxy for the ddtrace pytest-benchmark plugin."""

from . import _CIVISIBILITY_ENABLED


if _CIVISIBILITY_ENABLED:
    # CI Visibility enabled: import the actual ddtrace pytest-benchmark plugin
    from ddtrace.contrib.internal.pytest_benchmark.plugin import *  # noqa: F403,F401
