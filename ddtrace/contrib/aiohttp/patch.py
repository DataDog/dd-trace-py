import wrapt
import aiohttp_jinja2

from ddtrace.contrib import asyncio

from .template import _trace_template_rendering
from ...pin import Pin


def patch(tracer=None):
    """
    Patch aiohttp third party modules
    """
    if getattr(aiohttp_jinja2, '__datadog_patch', False):
        return
    setattr(aiohttp_jinja2, '__datadog_patch', True)

    # expect a tracer or use the asyncio default one
    tracer = tracer or asyncio.tracer

    # wrap the template engine and create the PIN object on the module
    _w = wrapt.wrap_function_wrapper
    _w('aiohttp_jinja2', 'render_template', _trace_template_rendering)
    Pin(app='aiohttp', service=None, app_type='web', tracer=tracer).onto(aiohttp_jinja2)
