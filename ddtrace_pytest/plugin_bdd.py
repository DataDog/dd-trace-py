"""Proxy for the ddtrace pytest-bdd plugin."""

from . import _CIVISIBILITY_ENABLED


if _CIVISIBILITY_ENABLED:
    # CI Visibility enabled: import the actual ddtrace pytest-bdd plugin
    from ddtrace.contrib.internal.pytest_bdd.plugin import *  # noqa: F403,F401
