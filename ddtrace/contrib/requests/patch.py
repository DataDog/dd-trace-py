import requests

from ddtrace import config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...pin import Pin
from ...utils.formats import asbool
from ...utils.formats import get_env
from ...utils.wrappers import unwrap as _u
from .connection import _wrap_send
from .legacy import _distributed_tracing
from .legacy import _distributed_tracing_setter


# requests default settings
config._add(
    "requests",
    {
        "distributed_tracing": asbool(get_env("requests", "distributed_tracing", default=True)),
        "split_by_domain": asbool(get_env("requests", "split_by_domain", default=False)),
    },
)


def patch():
    """Activate http calls tracing"""
    if getattr(requests, "__datadog_patch", False):
        return
    setattr(requests, "__datadog_patch", True)

    _w("requests", "Session.send", _wrap_send)
    Pin(app="requests", _config=config.requests).onto(requests.Session)

    # [Backward compatibility]: `session.distributed_tracing` should point and
    # update the `Pin` configuration instead. This block adds a property so that
    # old implementations work as expected
    fn = property(_distributed_tracing)
    fn = fn.setter(_distributed_tracing_setter)
    requests.Session.distributed_tracing = fn


def unpatch():
    """Disable traced sessions"""
    if not getattr(requests, "__datadog_patch", False):
        return
    setattr(requests, "__datadog_patch", False)

    _u(requests.Session, "send")
