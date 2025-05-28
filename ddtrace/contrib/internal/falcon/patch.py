import os

import falcon
import wrapt

from ddtrace import config
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.trace import tracer

from .middleware import TraceMiddleware


FALCON_VERSION = parse_version(falcon.__version__)


config._add(
    "falcon",
    dict(
        distributed_tracing=asbool(os.getenv("DD_FALCON_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(falcon, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"falcon": ">=3.0"}


def patch():
    """
    Patch falcon.API to include contrib.falcon.TraceMiddleware
    by default
    """
    if getattr(falcon, "_datadog_patch", False):
        return

    falcon._datadog_patch = True
    if FALCON_VERSION >= (3, 0, 0):
        wrapt.wrap_function_wrapper("falcon", "App.__init__", traced_init)
    if FALCON_VERSION < (4, 0, 0):
        wrapt.wrap_function_wrapper("falcon", "API.__init__", traced_init)


def traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop("middleware", [])
    service = config._get_service(default="falcon")

    mw.insert(0, TraceMiddleware(tracer, service))
    kwargs["middleware"] = mw

    wrapped(*args, **kwargs)
