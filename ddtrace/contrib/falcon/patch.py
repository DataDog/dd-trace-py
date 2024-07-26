import os

import falcon

from ddtrace import config
from ddtrace import tracer
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor import wrapt
from ddtrace.vendor.debtcollector import deprecate

from ...internal.utils.formats import asbool
from ...internal.utils.version import parse_version
from .middleware import TraceMiddleware


FALCON_VERSION = parse_version(falcon.__version__)


config._add(
    "falcon",
    dict(
        distributed_tracing=asbool(os.getenv("DD_FALCON_DISTRIBUTED_TRACING", default=True)),
    ),
)


def _get_version():
    # type: () -> str
    return getattr(falcon, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    """
    Patch falcon.API to include contrib.falcon.TraceMiddleware
    by default
    """
    if getattr(falcon, "_datadog_patch", False):
        return

    falcon._datadog_patch = True
    if FALCON_VERSION >= (3, 0, 0):
        wrapt.wrap_function_wrapper("falcon", "App.__init__", _traced_init)
    if FALCON_VERSION < (4, 0, 0):
        wrapt.wrap_function_wrapper("falcon", "API.__init__", _traced_init)


def _traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop("middleware", [])
    service = config._get_service(default="falcon")

    mw.insert(0, TraceMiddleware(tracer, service))
    kwargs["middleware"] = mw

    wrapped(*args, **kwargs)


def traced_init(wrapped, instance, args, kwargs):
    deprecate(
        "traced_init is deprecated",
        message="traced_init is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _traced_init(wrapped, instance, args, kwargs)
