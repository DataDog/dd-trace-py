from tornado import template

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.trace import tracer


def generate(func, renderer, args, kwargs):
    """
    Wrap the ``generate`` method used in templates rendering. Because the method
    may be called everywhere, the execution is traced in a tracer StackContext that
    inherits the current one if it's already available.
    """
    # get the module pin
    pin = Pin.get_from(template)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # change the resource and the template name
    # if it's created from a string instead of a file
    if "<string>" in renderer.name:
        resource = template_name = "render_string"
    else:
        resource = template_name = renderer.name

    # trace the original call
    with tracer.trace("tornado.template", resource=resource, span_type=SpanTypes.TEMPLATE) as span:
        set_service_and_source(span, pin.service, config.tornado)
        span._set_attribute(COMPONENT, config.tornado.integration_name)

        span._set_attribute("tornado.template_name", template_name)
        return func(*args, **kwargs)
