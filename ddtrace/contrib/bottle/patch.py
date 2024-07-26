import os

import bottle

from ddtrace import config
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor import wrapt
from ddtrace.vendor.debtcollector import deprecate

from ...internal.utils.formats import asbool
from .trace import TracePlugin


# Configure default configuration
config._add(
    "bottle",
    dict(
        distributed_tracing=asbool(os.getenv("DD_BOTTLE_DISTRIBUTED_TRACING", default=True)),
    ),
)


def _get_version():
    # type: () -> str
    return getattr(bottle, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    """Patch the bottle.Bottle class"""
    if getattr(bottle, "_datadog_patch", False):
        return

    bottle._datadog_patch = True
    wrapt.wrap_function_wrapper("bottle", "Bottle.__init__", _traced_init)


def _traced_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)

    service = config._get_service(default="bottle")

    plugin = TracePlugin(service=service)
    instance.install(plugin)


def traced_init(wrapped, instance, args, kwargs):
    deprecate(
        "traced_init is deprecated",
        message="traced_init is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _traced_init(wrapped, instance, args, kwargs)
