import wrapt

import molten
from molten.http import Request

from ... import Pin, config
from ...ext import AppTypes, http
from ...propagation.http import HTTPPropagator
from ...utils.formats import asbool, get_env
from ...utils.importlib import func_name
from ...utils.wrappers import unwrap


# Configure default configuration
config._add('molten', dict(
    service_name=get_env('molten', 'service_name', 'molten'),
    app='molten',
    app_type=AppTypes.web,
    distributed_tracing=asbool(get_env('molten', 'distributed_tracing', False)),
))

def trace_wrapped(resource, wrapped, *args, **kwargs):
    pin = Pin.get_from(molten)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with pin.tracer.trace(func_name(wrapped), service=pin.service, resource=resource):
        return wrapped(*args, **kwargs)


class WrapperComponent(wrapt.ObjectProxy):
    """ Tracing of components """
    def can_handle_parameter(self, *args, **kwargs):
        func = self.__wrapped__.can_handle_parameter
        cname = func_name(self.__wrapped__)
        resource = '{}.{}'.format(cname, func.__name__)
        return trace_wrapped(resource, func, *args, **kwargs)

    def resolve(self, *args, **kwargs):
        func = self.__wrapped__.resolve
        cname = func_name(self.__wrapped__)
        resource = '{}.{}'.format(cname, func.__name__)
        return trace_wrapped(resource, func, *args, **kwargs)


class WrapperRenderer(wrapt.ObjectProxy):
    """ Tracing of renderers """
    def render(self, *args, **kwargs):
        func = self.__wrapped__.render
        cname = func_name(self.__wrapped__)
        resource = '{}.{}'.format(cname, func.__name__)
        return trace_wrapped(resource, func, *args, **kwargs)


class WrapperMiddleware(wrapt.ObjectProxy):
    """ Tracing of middleware function or callable """
    def __call__(self, *args, **kwargs):
        func = self.__wrapped__.__call__
        cname = func_name(self.__wrapped__)
        # only use callable class name for resource name
        resource = '{}'.format(cname)
        return trace_wrapped(resource, func, *args, **kwargs)


def patch():
    """Patch the instrumented methods
    """
    if getattr(molten, '_datadog_patch', False):
        return
    setattr(molten, '_datadog_patch', True)

    pin = Pin(
        service=config.molten['service_name'],
        app=config.molten['app'],
        app_type=config.molten['app_type'],
    )

    # add pin to module since many classes use __slots__
    pin.onto(molten)

    _w = wrapt.wrap_function_wrapper
    _w('molten', 'BaseApp.__init__', patch_app_init)
    _w('molten', 'App.__call__', patch_app_call)
    _w('molten', 'Router.add_route', patch_add_route)


def unpatch():
    """Remove instrumentation
    """
    if getattr(molten, '_datadog_patch', False):
        setattr(molten, '_datadog_patch', False)

        # remove pin
        pin = Pin.get_from(molten)
        if pin:
            pin.remove_from(molten)

        unwrap(molten.BaseApp, '__init__')
        unwrap(molten.App, '__call__')
        unwrap(molten.Router, 'add_route')


def trace_func(resource):
    """Trace calls to function using provided resource name
    """
    @wrapt.function_wrapper
    def _trace_func(wrapped, instance, args, kwargs):
        pin = Pin.get_from(molten)

        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(func_name(wrapped), service=pin.service, resource=resource):
            return wrapped(*args, **kwargs)

    return _trace_func


@wrapt.function_wrapper
def patch_start_response(wrapped, instance, args, kwargs):
    """Patch respond handling to set metadata
    """
    pin = Pin.get_from(molten)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    span = pin.tracer.current_root_span()
    if not span:
        return wrapped(*args, **kwargs)

    status, headers, exc_info = args
    code, _, _ = status.partition(' ')

    try:
        code = int(code)
    except ValueError:
        pass

    span.set_tag(http.STATUS_CODE, code)

    # mark 5xx spans as error
    if 500 <= code < 600:
        span.error = 1

    return wrapped(*args, **kwargs)


def patch_add_route(wrapped, instance, args, kwargs):
    """Patch adding routes to trace route handler
    """
    def _wrap(route_like, prefix='', namespace=None):
        # avoid patching non-Route, e.g. Include
        if not isinstance(route_like, molten.Route):
            return wrapped(*args, **kwargs)

        resource = '{} {}'.format(
            route_like.method,
            route_like.template,
        )

        # patch handler for route
        route_like.handler = trace_func(resource)(route_like.handler)

        return wrapped(route_like, prefix=prefix, namespace=namespace)

    return _wrap(*args, **kwargs)


def patch_app_call(wrapped, instance, args, kwargs):
    """Patch wsgi interface for app
    """
    pin = Pin.get_from(molten)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # destruct arguments to wsgi app
    environ, start_response = args
    start_response = patch_start_response(start_response)
    method = environ.get('REQUEST_METHOD')
    path = environ.get('PATH_INFO')
    resource = func_name(wrapped)

    request = Request.from_environ(environ)
    distributed_tracing = asbool(environ.get('DD_MOLTEN_DISTRIBUTED_TRACING')) or \
        config.get_from(instance).get('distributed_tracing')
    if distributed_tracing:
        propagator = HTTPPropagator()
        context = propagator.extract(dict(request.headers))
        if context.trace_id:
            pin.tracer.context_provider.activate(context)

    with pin.tracer.trace('molten.request', service=pin.service, resource=resource) as span:
        span.set_tag(http.METHOD, method)
        span.set_tag(http.URL, path)
        span.set_tag('molten.version', molten.__version__)
        return wrapped(environ, start_response, **kwargs)


def patch_app_init(wrapped, instance, args, kwargs):
    """Patch app initialization of middleware, components and renderers
    """
    # allow instance to be initialized with middleware
    wrapped(*args, **kwargs)

    # add Pin to instance
    pin = Pin.get_from(molten)

    if not pin or not pin.enabled():
        return

    # trace middleware in instance
    instance.middleware = [
        WrapperMiddleware(mw)
        for mw in instance.middleware
    ]

    # patch class methods of component instances
    instance.components = [
        WrapperComponent(c)
        for c in instance.components
    ]

    # re-init dependency injector with wrapped components
    instance.injector = molten.DependencyInjector(
        components=instance.components,
        singletons={molten.BaseApp: instance},
    )

    # patch renderers
    instance.renderers = [
        WrapperRenderer(r)
        for r in instance.renderers
    ]
