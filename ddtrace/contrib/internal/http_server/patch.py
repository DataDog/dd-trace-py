from __future__ import annotations

import sys
from types import ModuleType
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable

from wrapt import wrap_function_wrapper as _w

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core


if TYPE_CHECKING:
    import http.server


def _get_http_server() -> ModuleType:
    # DEV: When patch() is called from the on-import hook, we're running from inside
    # http.server's own exec_module(), before CPython's import machinery binds it as the
    # "server" attribute of the "http" package (that setattr() happens only after
    # exec_module() returns to _find_and_load()). Accessing it via `http.server` (attribute
    # chain) at that point raises "cannot access submodule 'server' of module 'http' (most
    # likely due to a circular import)". sys.modules is populated before exec_module() even
    # starts, so prefer it -- falling back to a plain import for direct patch() calls that
    # happen before anything has imported http.server yet (not in sys.modules at all).
    loaded = sys.modules.get("http.server")
    if loaded is not None:
        return loaded
    import http.server

    return http.server


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"http.server": "*"}


def _wrap_parse_request(
    wrapped: Callable[..., bool],
    instance: "http.server.BaseHTTPRequestHandler",
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> bool:
    parsed = wrapped(*args, **kwargs)
    if parsed:
        core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (instance.command, instance.path))
    return parsed


def patch() -> None:
    http_server = _get_http_server()
    if getattr(http_server, "__datadog_patch", False):
        return
    http_server.__datadog_patch = True  # type: ignore[attr-defined]  # patch marker, not a real module attr

    _w(http_server.BaseHTTPRequestHandler, "parse_request", _wrap_parse_request)


def unpatch() -> None:
    http_server = _get_http_server()
    if not getattr(http_server, "__datadog_patch", False):
        return
    http_server.__datadog_patch = False  # type: ignore[attr-defined]  # patch marker, not a real module attr

    _u(http_server.BaseHTTPRequestHandler, "parse_request")
