import pylibmc

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from .client import TracedClient


# Original Client class
_Client = pylibmc.Client


def _get_version():
    # type: () -> str
    return getattr(pylibmc, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    pylibmc.Client = TracedClient


def unpatch():
    pylibmc.Client = _Client
