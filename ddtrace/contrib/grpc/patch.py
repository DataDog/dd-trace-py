import grpc

from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace import config, Pin

from ...utils.wrappers import unwrap as _u

from .client_interceptor import create_client_interceptor
from .server_interceptor import create_server_interceptor


config._add('grpc', dict(
    service_name='grpc',
    distributed_tracing_enabled=True,
))


def patch():
    # patch only once
    if getattr(grpc, '__datadog_patch', False):
        return
    setattr(grpc, '__datadog_patch', True)

    Pin(service=config.grpc.service_name).onto(grpc)

    _w('grpc', 'insecure_channel', _client_channel_interceptor)
    _w('grpc', 'secure_channel', _client_channel_interceptor)
    _w('grpc', 'server', _server_constructor_interceptor)


def unpatch():
    if not getattr(grpc, '__datadog_patch', False):
        return
    setattr(grpc, '__datadog_patch', False)

    pin = Pin.get_from(grpc)
    if pin:
        pin.remove_from(grpc)

    _u(grpc, 'secure_channel')
    _u(grpc, 'insecure_channel')
    _u(grpc, 'server')


def _client_channel_interceptor(wrapped, instance, args, kwargs):
    channel = wrapped(*args, **kwargs)

    (host, port) = _parse_target_from_arguments(args, kwargs)

    # DEV: we clone the pin on the grpc module and configure it for the client
    # interceptor
    pin = Pin.get_from(grpc)
    if not pin:
        return channel

    interceptor_function = create_client_interceptor(pin, host, port)
    channel = grpc.intercept_channel(channel, interceptor_function)

    return channel


def _server_constructor_interceptor(wrapped, instance, args, kwargs):
    # DEV: we clone the pin on the grpc module and configure it for the server
    # interceptor

    pin = Pin.get_from(grpc)
    if not pin:
        return wrapped(*args, **kwargs)

    interceptor = create_server_interceptor(pin)

    if 'interceptors' in kwargs:
        kwargs['interceptors'] = [interceptor] + kwargs['interceptors']
    else:
        kwargs['interceptors'] = [interceptor]

    server = wrapped(*args, **kwargs)

    return server


def _parse_target_from_arguments(args, kwargs):
    if 'target' in kwargs:
        target = kwargs['target']
    else:
        target = args[0]

    split = target.rsplit(':', 2)

    return (split[0], split[1] if len(split) > 1 else None)
