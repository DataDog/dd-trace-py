import requests
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import integration_hooks
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool

from .connection import _wrap_adapter_send
from .connection import _wrap_send
from .session import TracedSession


# requests default settings
config._add(
    "requests",
    {
        "distributed_tracing": asbool(env.get("DD_REQUESTS_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(env.get("DD_REQUESTS_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        "_default_service": schematize_service_name("requests"),
    },
)

# always patch our `TracedSession` when imported
_w(TracedSession, "send", _wrap_send)
# always wrapped, like TracedSession.send above; a no-op unless urllib3 tracing is enabled
_w(requests.adapters.HTTPAdapter, "send", _wrap_adapter_send)


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
    integration_hooks.on_patch("requests")


def unpatch():
    """Disable traced sessions"""
    if not getattr(requests, "__datadog_patch", False):
        return
    requests.__datadog_patch = False

    integration_hooks.on_unpatch("requests")
    try:
        _u(requests.Session, "send")
    except AttributeError:
        # It was not patched
        pass
