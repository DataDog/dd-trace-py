
import os

from ddtrace import config

from .trace import TracePlugin

import bottle

import wrapt

# Default settings
config._add(
    'bottle',
    {'service_name': os.environ.get('DATADOG_SERVICE_NAME') or 'bottle'},
)


def patch():
    """Patch the bottle.Bottle class
    """
    if getattr(bottle, '_datadog_patch', False):
        return

    setattr(bottle, '_datadog_patch', True)
    wrapt.wrap_function_wrapper('bottle', 'Bottle.__init__', traced_init)

def traced_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)

    plugin = TracePlugin(service=config.bottle['service_name'])
    instance.install(plugin)
