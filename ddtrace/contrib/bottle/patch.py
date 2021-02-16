import bottle

from ddtrace import config
from ddtrace.vendor import wrapt

from .trace import TracePlugin


def patch():
    """Patch the bottle.Bottle class"""
    if getattr(bottle, "_datadog_patch", False):
        return

    setattr(bottle, "_datadog_patch", True)
    wrapt.wrap_function_wrapper("bottle", "Bottle.__init__", traced_init)


def traced_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)

    service = config._get_service(default="bottle")

    plugin = TracePlugin(service=service)
    instance.install(plugin)
