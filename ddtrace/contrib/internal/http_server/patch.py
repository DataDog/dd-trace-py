import http.server
from typing import Any
from typing import Callable

from wrapt import wrap_function_wrapper as _w

from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal._identity import maybe_refresh_identity


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"http.server": "*"}


def _wrap_parse_request(
    wrapped: Callable[..., bool],
    instance: http.server.BaseHTTPRequestHandler,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> bool:
    parsed = wrapped(*args, **kwargs)
    if parsed:
        maybe_refresh_identity(instance.command, instance.path)
    return parsed


def patch() -> None:
    if getattr(http.server, "__datadog_patch", False):
        return
    http.server.__datadog_patch = True  # type: ignore[attr-defined]  # patch marker, not a real module attr

    _w(http.server.BaseHTTPRequestHandler, "parse_request", _wrap_parse_request)


def unpatch() -> None:
    if not getattr(http.server, "__datadog_patch", False):
        return
    http.server.__datadog_patch = False  # type: ignore[attr-defined]  # patch marker, not a real module attr

    _u(http.server.BaseHTTPRequestHandler, "parse_request")
