"""Proxy for the ddtrace pytest-benchmark plugin."""

from . import _CIVISIBILITY_ENABLED


# Simple two-fold branching: import from real plugin or disabled implementation
if _CIVISIBILITY_ENABLED:
    # Killswitch enabled: import the real ddtrace pytest-benchmark plugin
    from ddtrace.contrib.internal.pytest_benchmark.plugin import *  # noqa: F403,F401
else:
    # Killswitch disabled: import the no-op implementation
    from .disabled_pytest_benchmark import *  # noqa: F403,F401
