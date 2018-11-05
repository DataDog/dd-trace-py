import inspect
import os

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
    distributed_tracing_enabled=False,
))

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
    _w('molten', 'App.__init__', patch_app_init)
    _w('molten', 'App.__call__', patch_app_call)
    _w('molten', 'Router.add_route', patch_add_route)

def unpatch():
    """Remove instrumentation
    """
    if getattr(molten, '_datadog_patch', False):
        setattr(molten, '_datadog_patch', False)
        unwrap(molten.App, '__init__')
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

def trace_middleware(middleware):
    """Trace calling of middleware function or object
    """
    @wrapt.function_wrapper
    def _trace_middleware(wrapped, instance, args, kwargs):
        pin = Pin.get_from(molten)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        resource = None
        if inspect.isfunction(wrapped):
            resource = wrapped.__name__
        else:
            resource = type(wrapped).__name__

        return trace_func(resource)(wrapped(*args, **kwargs))

    return _trace_middleware(middleware)

def patch_start_response(start_response):
    """Patch respond handling to set metadata
    """
    @wrapt.function_wrapper
    def _start_response(wrapped, instance, args, kwargs):
        pin = Pin.get_from(molten)
        span = pin.tracer.current_root_span()
        status, headers, exc_info = args
        status_code, _, _ = status.partition(' ')
        span.set_tag(http.STATUS_CODE, status_code)
        return wrapped(*args, **kwargs)

    return _start_response(start_response)

def patch_add_route(wrapped, instance, args, kwargs):
    """Patch adding routes to trace route handler
    """
    def _wrap(route_like, prefix="", namespace=None):
        # avoid patching non-Route, e.g. Include
        if not isinstance(route_like, molten.Route):
            return wrapped(*args, **kwargs)

        resource = '{} {}'.format(
            route_like.method,
            route_like.name or route_like.template
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
    resource = u'{} {}'.format(method, path)

    # enable distributed tracing
    distributed_tracing = asbool(os.environ.get(
        'DATADOG_MOLTEN_DISTRIBUTED_TRACING')) or False
    request = Request.from_environ(environ)
    if distributed_tracing:
        propagator = HTTPPropagator()
        context = propagator.extract(request.headers)
        if context.trace_id:
            pin.tracer.context_provider.activate(context)

    with pin.tracer.trace('molten.request', service=pin.service, resource=resource) as span:
        span.set_tag(http.METHOD, method)
        span.set_tag(http.URL, path)
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
        trace_middleware(mw)
        for mw in instance.middleware
    ]

    # patch class methods of component instances
    for component in instance.components:
        component.__class__.can_handle_parameter = \
            trace_func(component.__class__.__name__)(component.can_handle_parameter)
        component.__class__.resolve = trace_func(component.__class__.__name__)(component.resolve)

    # patch renderers
    for renderers in instance.renderers:
        renderers.__class__.render = trace_func(renderers.__class__.__name__)(renderers.render)
