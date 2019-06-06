import grpc
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ddtrace import config, Pin

from ...ext import AppTypes
from ...utils.wrappers import unwrap as _u
from ...constants import ANALYTICS_SAMPLE_RATE_KEY

from .client_interceptor import client_interceptor_function


config._add('grpc', dict(
    service_name_client='grpc.client',
    app_client='grpc.client',
    service_name_server='grpc.server',
    app_server='grpc.server',
    span_type='grpc',
    app_type=AppTypes.web,

    distributed_tracing_enabled=True,
))


def patch():
    # patch only once
    if getattr(grpc, '__datadog_patch', False):
        return
    setattr(grpc, '__datadog_patch', True)

    # server_pin = Pin(
    #     service=config.grpc_server.service_name,
    #     app=config.grpc_server.app,
    #     app_type=config.grpc_server.app_type,
    # )

    _w('grpc', 'insecure_channel', _client_channel_interceptor)
    _w('grpc', 'secure_channel', _client_channel_interceptor)


def unpatch():
    if not getattr(grpc, '__datadog_patch', False):
        return
    setattr(grpc, '__datadog_patch', False)

    _u(grpc, 'secure_channel')
    _u(grpc, 'insecure_channel')


def _client_channel_interceptor(wrapped, instance, args, kwargs):
    channel = wrapped(*args, **kwargs)

    (host, port) = _parse_target_from_arguments(args, kwargs)

    tags = {
        'grpc.host': host,
        'grpc.port': port,
        ANALYTICS_SAMPLE_RATE_KEY: config.grpc.get_analytics_sample_rate()
    }

    pin = Pin(
        service=config.grpc.service_name_client,
        app=config.grpc.app_client,
        app_type=config.grpc.app_type,
        tags=tags,
    )

    channel = grpc.intercept_channel(channel, client_interceptor_function(pin))

    return channel


def _parse_target_from_arguments(args, kwargs):
    if 'target' in kwargs:
        target = kwargs['target']
    else:
        target = args[0]

    split = target.rsplit(':', 2)

    return (split[0], split[1] if len(split) > 1 else None)
