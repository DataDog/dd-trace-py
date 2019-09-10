import consul

from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...ext import AppTypes
from ...pin import Pin
from ...utils.wrappers import unwrap as _u


_KV_FUNCS = ['Consul.KV.put', 'Consul.KV.get', 'Consul.KV.delete']


def patch():
    if getattr(consul, '__datadog_patch', False):
        return
    setattr(consul, '__datadog_patch', True)

    pin = Pin(service='consul', app='consul', app_type=AppTypes.cache)
    pin.onto(consul.Consul.KV)

    for f_name in _KV_FUNCS:
        _w('consul', f_name, wrap_function(f_name))


def unpatch():
    if not getattr(consul, '__datadog_patch', False):
        return
    setattr(consul, '__datadog_patch', False)

    for f_name in _KV_FUNCS:
        name = f_name.split('.')[-1]
        _u(consul.Consul.KV, name)


def wrap_function(name):
    def trace_func(wrapped, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        # Only patch the syncronous implementation
        if not isinstance(instance.agent.http, consul.std.HTTPClient):
            return wrapped(*args, **kwargs)

        path = kwargs.get('key') or args[0]

        with pin.tracer.trace(name, service=pin.service, resource=path) as span:
            span.set_tag('consul.key', path)
            return wrapped(*args, **kwargs)

    return trace_func
