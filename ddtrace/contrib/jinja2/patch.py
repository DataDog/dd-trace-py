import jinja2
from wrapt import wrap_function_wrapper as _w

from ddtrace import config

from ...ext import http
from ...utils.formats import asbool, get_env
from ...pin import Pin
from ...utils.wrappers import unwrap as _u
from .constants import DEFAULT_SERVICE, DEFAULT_TEMPLATE_NAME


# default settings
config._add('jinja2',{
    'service_name': get_env('jinja2', 'service_name', DEFAULT_SERVICE),
    'inherit_service': asbool(get_env('jinja2', 'inherit_service', True)),
})


def patch():
    if getattr(jinja2, '__datadog_patch', False):
        # already patched
        return
    setattr(jinja2, '__datadog_patch', True)
    Pin(
        service=config.jinja2['service_name'],
        app='jinja2',
        app_type=http.TEMPLATE,
        _config=config.jinja2,
    ).onto(jinja2.environment.Environment)
    _w(jinja2, 'environment.Template.render', _get_render_wrapper('jinja2.render'))
    _w(jinja2, 'environment.Template.generate', _get_render_wrapper('jinja2.generate'))
    _w(jinja2, 'environment.Environment.compile', _wrap_compile)
    _w(jinja2, 'environment.Environment._load_template', _wrap_load_template)


def unpatch():
    if not getattr(jinja2, '__datadog_patch', False):
        return
    setattr(jinja2, '__datadog_patch', False)
    _u(jinja2.Template, 'render')
    _u(jinja2.Template, 'generate')
    _u(jinja2.Environment, 'compile')
    _u(jinja2.Environment, '_load_template')


def _get_service_name(environment, span):
    """if `inherit_service` is set then tries to get the service name of the
    parent span.
    The default value is set to `service_name` config variable (default: jinja2)
    """
    cfg = config.get_from(environment)
    if cfg['inherit_service']:
        if span._parent and span._parent.service:
            return span._parent.service
    return cfg['service_name']


def _get_render_wrapper(operation):
    def _wrap_render(wrapped, instance, args, kwargs):
        """Wrap `Template.render()` or `Template.generate()`
        """
        pin = Pin.get_from(instance.environment)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        template_name = instance.name or DEFAULT_TEMPLATE_NAME
        with pin.tracer.trace(operation, span_type=http.TEMPLATE) as span:
            # update the span service name before doing any action
            span.service = _get_service_name(instance.environment, span)
            try:
                return wrapped(*args, **kwargs)
            finally:
                span.resource = template_name
                span.set_tag('jinja2.template_name', template_name)
    return _wrap_render


def _wrap_compile(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    if len(args) > 1:
        template_name = args[1]
    else:
        template_name = kwargs.get('name', DEFAULT_TEMPLATE_NAME)

    with pin.tracer.trace('jinja2.compile', span_type=http.TEMPLATE) as span:
        # update the span service name before doing any action
        span.service = _get_service_name(instance, span)
        try:
            return wrapped(*args, **kwargs)
        finally:
            span.resource = template_name
            span.set_tag('jinja2.template_name', template_name)


def _wrap_load_template(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    template_name = kwargs.get('name', args[0])
    with pin.tracer.trace('jinja2.load', span_type=http.TEMPLATE) as span:
        # update the span service name before doing any action
        span.service = _get_service_name(instance, span)
        template = None
        try:
            template = wrapped(*args, **kwargs)
            return template
        finally:
            span.resource = template_name
            span.set_tag('jinja2.template_name', template_name)
            if template:
                span.set_tag('jinja2.template_path', template.filename)
