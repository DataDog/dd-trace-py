from typing import Dict

import pyramid
import pyramid.config
import wrapt

from ddtrace import config
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings._config import _get_config

from .constants import SETTINGS_DISTRIBUTED_TRACING
from .constants import SETTINGS_SERVICE
from .trace import DD_TWEEN_NAME
from .trace import trace_pyramid


config._add(
    "pyramid",
    dict(
        distributed_tracing=asbool(_get_config("DD_PYRAMID_DISTRIBUTED_TRACING", default=True)),
    ),
)

DD_PATCH = "_datadog_patch"


def get_version():
    # type: () -> str
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    return str(importlib_metadata.version(pyramid.__package__))


def _supported_versions() -> Dict[str, str]:
    return {"pyramid": ">=1.10"}


def patch():
    """
    Patch pyramid.config.Configurator
    """
    if getattr(pyramid, DD_PATCH, False):
        return
    setattr(pyramid, DD_PATCH, True)
    _w = wrapt.wrap_function_wrapper
    _w("pyramid.config", "Configurator.__init__", traced_init)


def traced_init(wrapped, instance, args, kwargs):
    settings = kwargs.pop("settings", {})
    service = config._get_service(default="pyramid")

    trace_settings = {
        SETTINGS_SERVICE: service,
        SETTINGS_DISTRIBUTED_TRACING: config.pyramid.distributed_tracing,
    }
    # Update over top of the defaults
    # DEV: If we did `settings.update(trace_settings)` then we would only ever
    #      have the default values.
    trace_settings.update(settings)
    # If the tweens are explicitly set with 'pyramid.tweens', we need to
    # explicitly set our tween too since `add_tween` will be ignored.
    insert_tween_if_needed(trace_settings)

    # The original Configurator.__init__ looks up two levels to find the package
    # name if it is not provided. This has to be replicated here since this patched
    # call will occur at the same level in the call stack.
    if not kwargs.get("package", None):
        from pyramid.path import caller_package

        kwargs["package"] = caller_package(level=2)

    kwargs["settings"] = trace_settings
    wrapped(*args, **kwargs)
    trace_pyramid(instance)


def insert_tween_if_needed(settings):
    tweens = settings.get("pyramid.tweens")
    # If the list is empty, pyramid does not consider the tweens have been
    # set explicitly.
    # And if our tween is already there, nothing to do
    if not tweens or not tweens.strip() or DD_TWEEN_NAME in tweens:
        return
    # pyramid.tweens.EXCVIEW is the name of built-in exception view provided by
    # pyramid.  We need our tween to be before it, otherwise unhandled
    # exceptions will be caught before they reach our tween.
    idx = tweens.find(pyramid.tweens.EXCVIEW)
    if idx == -1:
        settings["pyramid.tweens"] = tweens + "\n" + DD_TWEEN_NAME
    else:
        settings["pyramid.tweens"] = tweens[:idx] + DD_TWEEN_NAME + "\n" + tweens[idx:]
