import pylons
from pylons import config

from ddtrace import config as ddconfig
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...internal.utils import get_argument_value
from .compat import legacy_pylons
from .constants import CONFIG_MIDDLEWARE


def trace_rendering():
    """Patch all Pylons renderers. It supports multiple versions
    of Pylons and multiple renderers.
    """
    # patch only once
    if getattr(pylons.templating, "__datadog_patch", False):
        return
    pylons.templating.__datadog_patch = True

    if legacy_pylons:
        # Pylons <= 0.9.7
        _w("pylons.templating", "render", _traced_renderer)
    else:
        # Pylons > 0.9.7
        _w("pylons.templating", "render_mako", _traced_renderer)
        _w("pylons.templating", "render_mako_def", _traced_renderer)
        _w("pylons.templating", "render_genshi", _traced_renderer)
        _w("pylons.templating", "render_jinja2", _traced_renderer)


def _traced_renderer(wrapped, instance, args, kwargs):
    """Traced renderer"""
    tracer = config[CONFIG_MIDDLEWARE]._tracer
    with tracer.trace("pylons.render") as span:
        span.set_tag_str(COMPONENT, ddconfig.pylons.integration_name)

        template_name = get_argument_value(args, kwargs, 0, "template_name")
        span.set_tag_str("template.name", template_name)
        return wrapped(*args, **kwargs)
