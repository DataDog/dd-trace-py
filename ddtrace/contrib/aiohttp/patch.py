import wrapt
import aiohttp_jinja2

from .template import _trace_template_rendering
from ...pin import Pin

# TODO: the tracer should be found in a different way
from ...async import tracer


def patch():
    """
    Patch aiohttp third party modules
    """
    if getattr(aiohttp_jinja2, '__datadog_patch', False):
        return
    setattr(aiohttp_jinja2, '__datadog_patch', True)

    _w = wrapt.wrap_function_wrapper
    _w('aiohttp_jinja2', 'render_template', _trace_template_rendering)
    Pin(app='aiohttp', service=None, app_type='web', tracer=tracer).onto(aiohttp_jinja2)
