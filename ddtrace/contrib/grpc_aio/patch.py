import grpc

from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...utils.wrappers import unwrap as _u
from ..grpc import constants
from ..grpc import utils
from .aio_client_interceptor import create_aio_client_interceptors
from .aio_server_interceptor import create_aio_server_interceptor


config._add(
    "grpc_aio_server",
    dict(
        _default_service=constants.GRPC_AIO_SERVICE_SERVER,
        distributed_tracing_enabled=True,
    ),
)


config._add(
    "grpc_aio_client",
    dict(
        _default_service=constants.GRPC_AIO_SERVICE_CLIENT,
        distributed_tracing_enabled=True,
    ),
)


def patch():
    _patch_aio_client()
    _patch_aio_server()


def unpatch():
    _unpatch_aio_client()
    _unpatch_aio_server()


def _patch_aio_client():
    if getattr(constants.GRPC_AIO_PIN_MODULE_CLIENT, "__datadog_patch", False):
        return
    setattr(constants.GRPC_AIO_PIN_MODULE_CLIENT, "__datadog_patch", True)

    Pin().onto(constants.GRPC_AIO_PIN_MODULE_CLIENT)

    _w("grpc.aio", "insecure_channel", _aio_client_channel_interceptor)
    _w("grpc.aio", "secure_channel", _aio_client_channel_interceptor)


def _aio_client_channel_interceptor(wrapped, instance, args, kwargs):
    channel = wrapped(*args, **kwargs)

    pin = Pin.get_from(channel)
    if not pin or not pin.enabled():
        return channel

    (host, port) = utils._parse_target_from_args(args, kwargs)

    interceptors = create_aio_client_interceptors(pin, host, port)
    # DEV: Inject our tracing interceptor first in the list of interceptors
    if "interceptors" in kwargs:
        kwargs["interceptors"] = interceptors + tuple(kwargs["interceptors"])
    else:
        kwargs["interceptors"] = interceptors

    return wrapped(*args, **kwargs)


def _unpatch_aio_client():
    if not getattr(constants.GRPC_AIO_PIN_MODULE_CLIENT, "__datadog_patch", False):
        return
    setattr(constants.GRPC_AIO_PIN_MODULE_CLIENT, "__datadog_patch", False)

    pin = Pin.get_from(constants.GRPC_AIO_PIN_MODULE_CLIENT)
    if pin:
        pin.remove_from(constants.GRPC_AIO_PIN_MODULE_CLIENT)

    _u(grpc.aio, "insecure_channel")
    _u(grpc.aio, "secure_channel")


def _patch_aio_server():

    if getattr(constants.GRPC_AIO_PIN_MODULE_SERVER, "__datadog_patch", False):
        return
    setattr(constants.GRPC_AIO_PIN_MODULE_SERVER, "__datadog_patch", True)

    Pin().onto(constants.GRPC_AIO_PIN_MODULE_SERVER)

    _w("grpc.aio", "server", _aio_server_constructor_interceptor)


def _aio_server_constructor_interceptor(wrapped, instance, args, kwargs):
    pin = Pin.get_from(constants.GRPC_AIO_PIN_MODULE_SERVER)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    interceptor = create_aio_server_interceptor(pin)
    # DEV: Inject our tracing interceptor first in the list of interceptors
    if "interceptors" in kwargs:
        kwargs["interceptors"] = (interceptor,) + tuple(kwargs["interceptors"])
    else:
        kwargs["interceptors"] = (interceptor,)

    return wrapped(*args, **kwargs)


def _unpatch_aio_server():
    if not getattr(constants.GRPC_AIO_PIN_MODULE_SERVER, "__datadog_patch", False):
        return
    setattr(constants.GRPC_AIO_PIN_MODULE_SERVER, "__datadog_patch", False)

    pin = Pin.get_from(constants.GRPC_AIO_PIN_MODULE_SERVER)
    if pin:
        pin.remove_from(constants.GRPC_AIO_PIN_MODULE_SERVER)

    _u(grpc.aio, "server")
