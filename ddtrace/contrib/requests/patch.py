import os

import requests

from ddtrace import config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...internal.utils.formats import asbool
from ...pin import Pin
from ..trace_utils import unwrap as _u
from .connection import _wrap_send


# requests default settings
config._add(
    "requests",
    {
        "distributed_tracing": asbool(os.getenv("DD_REQUESTS_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(os.getenv("DD_REQUESTS_SPLIT_BY_DOMAIN", default=False)),
        "_default_service": "requests",
    },
)


def patch():
    """Activate http calls tracing"""
    if getattr(requests, "__datadog_patch", False):
        return
    setattr(requests, "__datadog_patch", True)

    _w("requests", "Session.send", _wrap_send)
    Pin(_config=config.requests).onto(requests.Session)


def unpatch():
    """Disable traced sessions"""
    if not getattr(requests, "__datadog_patch", False):
        return
    setattr(requests, "__datadog_patch", False)

    _u(requests.Session, "send")
