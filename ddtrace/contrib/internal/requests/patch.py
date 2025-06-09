import os
from typing import Dict

import requests
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Pin

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


def get_version():
    # type: () -> str
    return getattr(requests, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"requests": ">=2.23.0"}


def patch():
    """Activate http calls tracing"""
    if getattr(requests, "__datadog_patch", False):
        return
    requests.__datadog_patch = True

    _w("requests", "Session.send", _wrap_send)
    # IAST needs to wrap this function because `Session.send` is too late
    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_request

        _w("requests", "Session.request", _wrap_request)
    Pin(_config=config.requests).onto(requests.Session)

    if asm_config._iast_enabled:
        from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
        from ddtrace.appsec._iast.constants import VULN_SSRF

        _set_metric_iast_instrumented_sink(VULN_SSRF)


def unpatch():
    """Disable traced sessions"""
    if not getattr(requests, "__datadog_patch", False):
        return
    requests.__datadog_patch = False

    try:
        _u(requests.Session, "request")
    except AttributeError:
        # It was not patched
        pass
    try:
        _u(requests.Session, "send")
    except AttributeError:
        # It was not patched
        pass
