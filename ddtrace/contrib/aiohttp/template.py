"""
Instrumenting aiohttp_jinja2 external module
TODO: better docstring
"""
import aiohttp_jinja2

from ddtrace import Pin


def _trace_template_rendering(func, module, args, kwargs):
    """
    Trace the template rendering
    """
    # get the module pin
    pin = Pin.get_from(aiohttp_jinja2)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # extract span metas
    request = args[1]
    env = aiohttp_jinja2.get_env(request.app)
    template_prefix = env.loader.package_path
    template_name = args[0]
    template_meta = '{}/{}'.format(template_prefix, template_name)

    with pin.tracer.trace('aiohttp.render_template') as span:
        span.set_meta('aiohttp.template_name', template_meta)
        return func(*args, **kwargs)
