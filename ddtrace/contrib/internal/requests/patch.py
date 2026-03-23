import os

import requests
import urllib3
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.contrib.internal.urllib3.patch import _wrap_make_request
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool

from .connection import _wrap_send
from .session import TracedSession


# requests default settings
config._add(
    "requests",
    {
        "distributed_tracing": asbool(os.getenv("DD_REQUESTS_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(os.getenv("DD_REQUESTS_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        "_default_service": schematize_service_name("requests"),
    },
)

# always patch our `TracedSession` when imported
_w(TracedSession, "send", _wrap_send)
Pin(_config=config.requests).onto(TracedSession)


def get_version() -> str:
    return getattr(requests, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"requests": ">=2.25.1"}


def patch():
    """Activate http calls tracing"""
    if getattr(requests, "__datadog_patch", False):
        return
    requests.__datadog_patch = True

    _w("requests", "Session.send", _wrap_send)
    if not trace_utils.iswrapped(urllib3.connectionpool.HTTPConnectionPool, "_make_request"):
        _w("urllib3.connectionpool", "HTTPConnectionPool._make_request", _wrap_make_request)
    Pin(_config=config.requests).onto(requests.Session)


def unpatch():
    """Disable traced sessions"""
    if not getattr(requests, "__datadog_patch", False):
        return
    requests.__datadog_patch = False

    try:
        _u(requests.Session, "send")
    except AttributeError:
        # It was not patched
        pass
    if not getattr(urllib3, "__datadog_patch", False):
        try:
            _u(urllib3.connectionpool.HTTPConnectionPool, "_make_request")
        except AttributeError:
            pass
