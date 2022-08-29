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


try:
    # `grpc.aio` is only available with `grpcio>=1.32`.
    import grpc.aio

    from .aio_client_interceptor import create_aio_client_interceptors
    from .aio_server_interceptor import create_aio_server_interceptor

    HAS_GRPC_AIO = True
    # NOTE: These are not defined in constants.py because we would end up having
    # try-except in both files.
    GRPC_AIO_PIN_MODULE_SERVER = grpc.aio.Server
    GRPC_AIO_PIN_MODULE_CLIENT = grpc.aio.Channel
except ImportError:
    HAS_GRPC_AIO = False
    # NOTE: These are defined just to prevent a 'not defined' error.
    # Be sure not to use them when `HAS_GRPC_AIO` is False.
    GRPC_AIO_PIN_MODULE_SERVER = None
    GRPC_AIO_PIN_MODULE_CLIENT = None


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


if HAS_GRPC_AIO:
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
    _patch_client()
    _patch_server()
    if HAS_GRPC_AIO:
        _patch_aio_client()
        _patch_aio_server()


def unpatch():
    _unpatch_client()
    _unpatch_server()
    if HAS_GRPC_AIO:
        _unpatch_aio_client()
        _unpatch_aio_server()


def _patch_client():
    if getattr(constants.GRPC_PIN_MODULE_CLIENT, "__datadog_patch", False):
        return
    setattr(constants.GRPC_PIN_MODULE_CLIENT, "__datadog_patch", True)

    Pin().onto(constants.GRPC_PIN_MODULE_CLIENT)

    _w("grpc", "insecure_channel", _client_channel_interceptor)
    _w("grpc", "secure_channel", _client_channel_interceptor)
    _w("grpc", "intercept_channel", intercept_channel)


def _patch_aio_client():
    if getattr(GRPC_AIO_PIN_MODULE_CLIENT, "__datadog_patch", False):
        return
    setattr(GRPC_AIO_PIN_MODULE_CLIENT, "__datadog_patch", True)

    Pin().onto(GRPC_AIO_PIN_MODULE_CLIENT)

    _w("grpc.aio", "insecure_channel", _aio_client_channel_interceptor)
    _w("grpc.aio", "secure_channel", _aio_client_channel_interceptor)


def _unpatch_client():
    if not getattr(constants.GRPC_PIN_MODULE_CLIENT, "__datadog_patch", False):
        return
    setattr(constants.GRPC_PIN_MODULE_CLIENT, "__datadog_patch", False)

    pin = Pin.get_from(constants.GRPC_PIN_MODULE_CLIENT)
    if pin:
        pin.remove_from(constants.GRPC_PIN_MODULE_CLIENT)

    _u(grpc, "secure_channel")
    _u(grpc, "insecure_channel")
    _u(grpc, "intercept_channel")


def _unpatch_aio_client():
    if not getattr(GRPC_AIO_PIN_MODULE_CLIENT, "__datadog_patch", False):
        return
    setattr(GRPC_AIO_PIN_MODULE_CLIENT, "__datadog_patch", False)

    pin = Pin.get_from(GRPC_AIO_PIN_MODULE_CLIENT)
    if pin:
        pin.remove_from(GRPC_AIO_PIN_MODULE_CLIENT)

    _u(grpc.aio, "insecure_channel")
    _u(grpc.aio, "secure_channel")


def _patch_server():
    if getattr(constants.GRPC_PIN_MODULE_SERVER, "__datadog_patch", False):
        return
    setattr(constants.GRPC_PIN_MODULE_SERVER, "__datadog_patch", True)

    Pin().onto(constants.GRPC_PIN_MODULE_SERVER)

    _w("grpc", "server", _server_constructor_interceptor)


def _patch_aio_server():
    if getattr(GRPC_AIO_PIN_MODULE_SERVER, "__datadog_patch", False):
        return
    setattr(GRPC_AIO_PIN_MODULE_SERVER, "__datadog_patch", True)

    Pin().onto(GRPC_AIO_PIN_MODULE_SERVER)

    _w("grpc.aio", "server", _aio_server_constructor_interceptor)


def _unpatch_server():
    if not getattr(constants.GRPC_PIN_MODULE_SERVER, "__datadog_patch", False):
        return
    setattr(constants.GRPC_PIN_MODULE_SERVER, "__datadog_patch", False)

    pin = Pin.get_from(constants.GRPC_PIN_MODULE_SERVER)
    if pin:
        pin.remove_from(constants.GRPC_PIN_MODULE_SERVER)

    _u(grpc, "server")


def _unpatch_aio_server():
    if not getattr(GRPC_AIO_PIN_MODULE_SERVER, "__datadog_patch", False):
        return
    setattr(GRPC_AIO_PIN_MODULE_SERVER, "__datadog_patch", False)

    pin = Pin.get_from(GRPC_AIO_PIN_MODULE_SERVER)
    if pin:
        pin.remove_from(GRPC_AIO_PIN_MODULE_SERVER)

    _u(grpc.aio, "server")


def _client_channel_interceptor(wrapped, instance, args, kwargs):
    channel = wrapped(*args, **kwargs)

    pin = Pin.get_from(channel)
    if not pin or not pin.enabled():
        return channel

    (host, port) = utils._parse_target_from_args(args, kwargs)

    interceptor_function = create_client_interceptor(pin, host, port)
    return grpc.intercept_channel(channel, interceptor_function)


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


def _aio_server_constructor_interceptor(wrapped, instance, args, kwargs):
    pin = Pin.get_from(GRPC_AIO_PIN_MODULE_SERVER)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    interceptor = create_aio_server_interceptor(pin)
    # DEV: Inject our tracing interceptor first in the list of interceptors
    if "interceptors" in kwargs:
        kwargs["interceptors"] = (interceptor,) + tuple(kwargs["interceptors"])
    else:
        kwargs["interceptors"] = (interceptor,)

    return wrapped(*args, **kwargs)
