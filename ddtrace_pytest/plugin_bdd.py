"""Proxy for the ddtrace pytest-bdd plugin."""

from . import _CIVISIBILITY_ENABLED


# Simple two-fold branching: import from real plugin or disabled implementation
if _CIVISIBILITY_ENABLED:
    # Killswitch enabled: import the real ddtrace pytest-bdd plugin
    from ddtrace.contrib.internal.pytest_bdd.plugin import *  # noqa: F403,F401
else:
    # Killswitch disabled: import the no-op implementation
    from .disabled_pytest_bdd import *  # noqa: F403,F401
