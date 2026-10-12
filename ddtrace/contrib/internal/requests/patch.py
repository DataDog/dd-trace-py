from typing import Any
from typing import Callable

import requests
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool

from .connection import _wrap_adapter_send
from .connection import _wrap_send
from .session import TracedSession


_REQUEST_STARTED = "context.started." + HttpClientEvents.REQUESTS_REQUEST.value


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


def _wrap_request(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if not core.has_listeners(_REQUEST_STARTED):
        return func(*args, **kwargs)

    with core.context_with_event(
        HttpClientRequestEvent(
            component=config.requests.integration_name,
            integration_config=config.requests,
            http_operation="requests.request",
            request_url=args[1] if len(args) > 1 else kwargs.get("url"),
            request_method=args[0] if args else kwargs.get("method"),
            request_headers=kwargs.get("headers") or instance.headers,
        ),
        # This observes API inputs before preparation; Session.send owns tracing and propagation.
        context_name_override=HttpClientEvents.REQUESTS_REQUEST.value,
    ) as ctx:
        ctx.event.response = func(*args, **kwargs)
        return ctx.event.response


def patch():
    """Activate http calls tracing"""
    if getattr(requests, "__datadog_patch", False):
        return
    requests.__datadog_patch = True

    _w("requests", "Session.send", _wrap_send)
    _w("requests", "Session.request", _wrap_request)


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
