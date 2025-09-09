"""Proxy for the main ddtrace pytest plugin."""

from . import _CIVISIBILITY_ENABLED


# Two-fold branching, depending on killswitch: import from real plugin or disabled implementation
if _CIVISIBILITY_ENABLED:
    from ddtrace.contrib.internal.pytest.plugin import *  # noqa: F403,F401
else:
    from .disabled_pytest import *  # noqa: F403,F401
