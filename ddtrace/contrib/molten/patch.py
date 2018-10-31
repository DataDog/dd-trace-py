import inspect

import wrapt

import molten
from ddtrace import Pin, config
from ddtrace.utils.formats import get_env
from ddtrace.utils.importlib import func_name

from ...ext import AppTypes

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

def trace_func(resource):
    @wrapt.function_wrapper
    def _trace_func(wrapped, instance, args, kwargs):
        pin = Pin.get_from(molten)

        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(func_name(wrapped), service=pin.service, resource=resource):
            return wrapped(*args, **kwargs)

    return _trace_func

def trace_middleware(middleware):
    @wrapt.function_wrapper
    def _trace_middleware(wrapped, instance, args, kwargs):
        pin = Pin.get_from(molten)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        name = None
        if inspect.isfunction(wrapped):
            name = wrapped.__name__
        else:
            name = type(wrapped).__name__

        with pin.tracer.trace(name, service=pin.service):
            return trace_func(name)(wrapped(*args, **kwargs))

    return _trace_middleware(middleware)


def patch_add_route(wrapped, instance, args, kwargs):
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
    pin = Pin.get_from(molten)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # trace call
    with pin.tracer.trace(func_name(wrapped), service=pin.service):
        return wrapped(*args, **kwargs)


def patch_app_init(wrapped, instance, args, kwargs):
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
