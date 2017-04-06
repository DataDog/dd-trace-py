from tornado import template

from ddtrace import Pin

from ...ext import http


def generate(func, renderer, args, kwargs):
    """
    Wrap the ``generate`` method used in templates rendering. Because the method
    may be called everywhere, the execution is traced in a tracer StackContext that
    inherits the current one if it's already available.
    TODO
    """
    # get the module pin
    pin = Pin.get_from(template)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # trace the original call
    with pin.tracer.trace('tornado.template', service=pin.service) as span:
        span.span_type = http.TEMPLATE
        span.resource = renderer.name
        span.set_meta('tornado.template_name', renderer.name)
        return func(*args, **kwargs)
