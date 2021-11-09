from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.vendor import wrapt

from ...internal.utils.wrappers import unwrap
from ...pin import Pin


log = get_logger(__name__)

try:
    # instrument external packages only if they're available
    import aiohttp_jinja2

    from .template import _trace_render_template

    template_module = True
except ImportError:
    template_module = False
except Exception:
    log.warning("aiohttp_jinja2 could not be imported and will not be instrumented.", exc_info=True)
    template_module = False

config._add(
    "aiohttp",
    dict(
        distributed_tracing=True,
    ),
)


def patch():
    """
    Patch aiohttp third party modules:
        * aiohttp_jinja2
    """
    if template_module:
        if getattr(aiohttp_jinja2, "__datadog_patch", False):
            return
        setattr(aiohttp_jinja2, "__datadog_patch", True)

        _w = wrapt.wrap_function_wrapper
        _w("aiohttp_jinja2", "render_template", _trace_render_template)
        Pin(app="aiohttp", service=config.service).onto(aiohttp_jinja2)


def unpatch():
    """
    Remove tracing from patched modules.
    """
    if template_module:
        if getattr(aiohttp_jinja2, "__datadog_patch", False):
            setattr(aiohttp_jinja2, "__datadog_patch", False)
            unwrap(aiohttp_jinja2, "render_template")
