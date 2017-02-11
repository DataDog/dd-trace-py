import wrapt
import aiohttp_jinja2

from .template import _trace_template_rendering
from ...pin import Pin


def patch():
    """
    Patch aiohttp third party modules
    """
    if getattr(aiohttp_jinja2, '__datadog_patch', False):
        return
    setattr(aiohttp_jinja2, '__datadog_patch', True)

    _w = wrapt.wrap_function_wrapper
    _w('aiohttp_jinja2', 'render_template', _trace_template_rendering)
    Pin(app='aiohttp', service=None, app_type='web').onto(aiohttp_jinja2)


def unpatch():
    if getattr(aiohttp_jinja2, '__datadog_patch', False):
        setattr(aiohttp_jinja2, '__datadog_patch', False)
        _unwrap(aiohttp_jinja2, 'render_template')


def _unwrap(obj, attr):
    f = getattr(obj, attr, None)
    if f and isinstance(f, wrapt.ObjectProxy) and hasattr(f, '__wrapped__'):
        setattr(obj, attr, f.__wrapped__)
