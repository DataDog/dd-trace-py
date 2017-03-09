import os

from .trace import trace_pyramid

import pyramid.config


def patch():
    """
    Patch pyramid.config.Configurator
    """
    if getattr(pyramid.config, '_datadog_patch', False):
        return

    setattr(pyramid.config, '_datadog_patch', True)
    setattr(pyramid.config, 'Configurator', TracedConfigurator)


class TracedConfigurator(pyramid.config.Configurator):

    def __init__(self, *args, **kwargs):
        settings = kwargs.pop("settings", {})
        service = os.environ.get("DATADOG_SERVICE_NAME") or "pyramid"
        trace_settings = {
            'datadog_trace_service' : service,
        }
        settings.update(trace_settings)
        kwargs["settings"] = settings

        super(TracedConfigurator, self).__init__(*args, **kwargs)
        trace_pyramid(self)
