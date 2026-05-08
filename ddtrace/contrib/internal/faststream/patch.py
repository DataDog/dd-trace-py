from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import faststream
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u

from ._middleware import _DDTraceMiddleware
from ._middleware import detect_messaging_system


config._add(
    "faststream",
    {
        "_default_service": schematize_service_name("faststream"),
        "distributed_tracing_enabled": asbool(env.get("DD_FASTSTREAM_DISTRIBUTED_TRACING", default=True)),
    },
)


def get_version() -> str:
    try:
        return _pkg_version("faststream")
    except PackageNotFoundError:
        return ""


def _supported_versions() -> dict[str, str]:
    return {"faststream": ">=0.6.0"}


# AIDEV-NOTE: We auto-instrument by appending a single Datadog middleware to the
# broker's middleware chain. FastStream's middleware contract is the public
# extension point used by FastStream's own OpenTelemetry integration, so we are
# riding on a stable surface rather than wrapping internal handler dispatch.
def _traced_broker_init(func, instance, args, kwargs):
    func(*args, **kwargs)
    config_obj = getattr(instance, "config", None)
    if config_obj is None:
        return

    existing = tuple(getattr(config_obj, "broker_middlewares", ()) or ())
    if any(isinstance(m, _DDTraceMiddleware) for m in existing):
        return

    middleware = _DDTraceMiddleware(
        messaging_system=detect_messaging_system(instance),
        broker=instance,
    )
    add = getattr(config_obj, "add_middleware", None)
    if callable(add):
        add(middleware)
    else:
        config_obj.broker_middlewares = (*existing, middleware)


def patch():
    if getattr(faststream, "_datadog_patch", False):
        return
    faststream._datadog_patch = True

    _w("faststream._internal.broker.broker", "BrokerUsecase.__init__", _traced_broker_init)


def unpatch():
    if not getattr(faststream, "_datadog_patch", False):
        return
    faststream._datadog_patch = False

    from faststream._internal.broker.broker import BrokerUsecase

    _u(BrokerUsecase, "__init__")
