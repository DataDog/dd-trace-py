"""
Datadog APM integration for OpenAI Agents SDK.
"""
from typing import TYPE_CHECKING

from ddtrace import config
from ddtrace.internal.utils.importlib import require_modules


required_modules = ["agents"]

if TYPE_CHECKING:  # pragma: no cover
    from agents import Agent  # noqa:F401


def get_version():
    """
    Get the version of the OpenAI Agents SDK
    """
    # Since this is a new SDK, we'll need to implement version detection
    # once we know how it's exposed in the package
    return ""


def patch():
    """
    Patch the instrumented methods
    """
    if not require_modules(required_modules):
        return

    from ddtrace.contrib.internal.agents.patch import patch as _patch

    _patch()


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    from ddtrace.contrib.internal.agents.patch import unpatch as _unpatch

    _unpatch() 