import molten
from ddtrace import config, Pin
from ddtrace.utils.formats import get_env
from ...ext import AppTypes
import inspect

import wrapt

# Configure default configuration
config._add('molten', dict(
    service_name=get_env('molten', 'service_name'),
    app='molten',
    app_type=AppTypes.web,
    distributed_tracing_enabled=False,
))

def patch():
    """Patch the instrumented methods

    This duplicated doesn't look nice. The nicer alternative is to use an ObjectProxy on top
    of Redis and StrictRedis. However, it means that any "import redis.Redis" won't be instrumented.
    """
    if getattr(molten, '_datadog_patch', False):
        return
    setattr(molten, '_datadog_patch', True)

    Pin(
        service=config.molten['service_name'],
        app=config.molten['app'],
        app_type=config.molten['app_type'],
    ).onto(molten.App)

    _w = wrapt.wrap_function_wrapper
    _w('molten', 'App.__init__', trace_app_init)
    _w('molten', 'App.__call__', trace_app_call)

def trace_app_call(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with pin.tracer.trace('molten.request', service='molten'):
        return wrapped(*args, **kwargs)

def wrap_middleware(instance, middleware):
    # we want to trace both the __call__ as well as the function that is returned

    @wrapt.function_wrapper
    def trace_middleware(wrapped, _instance, args, kwargs):

        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)


        name = None
        if inspect.isfunction(wrapped):
            name = wrapped.__name__
        else:
            name = type(wrapped).__name__

        import pdb; pdb.set_trace()

        with pin.tracer.trace(name, service='molten'):
            return wrapped(*args, **kwargs)

    return trace_middleware(middleware, instance)

def trace_app_init(wrapped, instance, args, kwargs):
    # allow instance to be initialized with middleware
    wrapped(*args, **kwargs)

    # add Pin to instance
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return

    # wrap each middleware in instance
    instance.middleware = [
        wrap_middleware(instance, mw)
        for mw in instance.middleware
    ]
    pass
