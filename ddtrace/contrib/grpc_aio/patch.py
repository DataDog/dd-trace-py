import grpc

from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...utils.wrappers import unwrap as _u
from ..grpc import constants
from .aio_server_interceptor import create_aio_server_interceptor


config._add(
    "grpc_aio_server",
    dict(
        _default_service=constants.GRPC_AIO_SERVICE_SERVER,
        distributed_tracing_enabled=True,
    ),
)


def patch():
    _patch_aio_server()


def unpatch():
    _unpatch_aio_server()


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
