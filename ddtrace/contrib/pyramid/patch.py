import os

from .trace import trace_pyramid

import pyramid.config

import wrapt


def patch():
    """
    Patch pyramid.config.Configurator
    """
    if getattr(pyramid.config, '_datadog_patch', False):
        return

    setattr(pyramid.config, '_datadog_patch', True)
    _w = wrapt.wrap_function_wrapper
    _w('pyramid.config', 'Configurator.__init__', traced_init)


def traced_init(wrapped, instance, args, kwargs):
    settings = kwargs.pop("settings", {})
    service = os.environ.get("DATADOG_SERVICE_NAME") or "pyramid"
    trace_settings = {
        'datadog_trace_service' : service,
    }
    settings.update(trace_settings)
    kwargs["settings"] = settings

    wrapped(*args, **kwargs)
    trace_pyramid(instance)
