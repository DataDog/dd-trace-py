import grpc
import wrapt

from ddtrace import Pin
from ...utils.wrappers import unwrap

from .client_interceptor import GrpcClientInterceptor


def patch():
    # patch only once
    if getattr(grpc, '__datadog_patch', False):
        return
    setattr(grpc, '__datadog_patch', True)
    Pin(service='grpc', app='grpc', app_type='grpc').onto(grpc)

    _w = wrapt.wrap_function_wrapper

    _w('grpc', 'insecure_channel', _insecure_channel_with_interceptor)
    _w('grpc', 'secure_channel', _secure_channel_with_interceptor)


def unpatch():
    if not getattr(grpc, '__datadog_patch', False):
        return
    setattr(grpc, '__datadog_patch', False)
    unwrap(grpc, 'secure_channel')
    unwrap(grpc, 'insecure_channel')


def _insecure_channel_with_interceptor(wrapped, instance, args, kwargs):
    channel = wrapped(*args, **kwargs)
    target = args[0]
    (host, port) = get_host_port(target)
    channel = _intercept_channel(channel, host, port)
    return channel


def _secure_channel_with_interceptor(wrapped, instance, args, kwargs):
    channel = wrapped(*args, **kwargs)
    target = args[0]
    (host, port) = get_host_port(target)
    channel = _intercept_channel(channel, host, port)
    return channel


def _intercept_channel(channel, host, port):
    return grpc.intercept_channel(channel, GrpcClientInterceptor(host, port))


def get_host_port(target):
    split = target.rsplit(':', 2)

    return (split[0], split[1] if len(split) > 1 else None)
