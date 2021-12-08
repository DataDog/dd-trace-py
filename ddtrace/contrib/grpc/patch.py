import grpc

from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from . import constants
from . import utils
from ..trace_utils import unwrap as _u
from .client_interceptor import create_client_interceptor
from .client_interceptor import intercept_channel
from .server_interceptor import create_server_interceptor


config._add(
    "grpc_server",
    dict(
        _default_service=constants.GRPC_SERVICE_SERVER,
        distributed_tracing_enabled=True,
    ),
)


# TODO[tbutt]: keeping name for client config unchanged to maintain backwards
# compatibility but should change in future
config._add(
    "grpc",
    dict(
        _default_service=constants.GRPC_SERVICE_CLIENT,
        distributed_tracing_enabled=True,
    ),
)


def patch():
    _patch_client()
    _patch_server()


def unpatch():
    _unpatch_client()
    _unpatch_server()


def _patch_client():
    if getattr(constants.GRPC_PIN_MODULE_CLIENT, "__datadog_patch", False):
        return
    setattr(constants.GRPC_PIN_MODULE_CLIENT, "__datadog_patch", True)

    Pin().onto(constants.GRPC_PIN_MODULE_CLIENT)

    _w("grpc", "insecure_channel", _client_channel_interceptor)
    _w("grpc", "secure_channel", _client_channel_interceptor)
    _w("grpc", "intercept_channel", intercept_channel)


def _unpatch_client():
    if not getattr(constants.GRPC_PIN_MODULE_CLIENT, "__datadog_patch", False):
        return
    setattr(constants.GRPC_PIN_MODULE_CLIENT, "__datadog_patch", False)

    pin = Pin.get_from(constants.GRPC_PIN_MODULE_CLIENT)
    if pin:
        pin.remove_from(constants.GRPC_PIN_MODULE_CLIENT)

    _u(grpc, "secure_channel")
    _u(grpc, "insecure_channel")


def _patch_server():
    if getattr(constants.GRPC_PIN_MODULE_SERVER, "__datadog_patch", False):
        return
    setattr(constants.GRPC_PIN_MODULE_SERVER, "__datadog_patch", True)

    Pin().onto(constants.GRPC_PIN_MODULE_SERVER)

    _w("grpc", "server", _server_constructor_interceptor)


def _unpatch_server():
    if not getattr(constants.GRPC_PIN_MODULE_SERVER, "__datadog_patch", False):
        return
    setattr(constants.GRPC_PIN_MODULE_SERVER, "__datadog_patch", False)

    pin = Pin.get_from(constants.GRPC_PIN_MODULE_SERVER)
    if pin:
        pin.remove_from(constants.GRPC_PIN_MODULE_SERVER)

    _u(grpc, "server")


def _client_channel_interceptor(wrapped, instance, args, kwargs):
    channel = wrapped(*args, **kwargs)

    pin = Pin.get_from(channel)
    if not pin or not pin.enabled():
        return channel

    (host, port) = utils._parse_target_from_args(args, kwargs)

    interceptor_function = create_client_interceptor(pin, host, port)
    return grpc.intercept_channel(channel, interceptor_function)


def _server_constructor_interceptor(wrapped, instance, args, kwargs):
    # DEV: we clone the pin on the grpc module and configure it for the server
    # interceptor

    pin = Pin.get_from(constants.GRPC_PIN_MODULE_SERVER)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    interceptor = create_server_interceptor(pin)

    # DEV: Inject our tracing interceptor first in the list of interceptors
    if "interceptors" in kwargs:
        kwargs["interceptors"] = (interceptor,) + tuple(kwargs["interceptors"])
    else:
        kwargs["interceptors"] = (interceptor,)

    return wrapped(*args, **kwargs)
