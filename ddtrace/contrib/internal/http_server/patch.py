from __future__ import annotations

import http.server as http_server
from typing import Any
from typing import Callable

from wrapt import wrap_function_wrapper as _w

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core
from ddtrace.internal.serverless import in_aws_lambda_microvm


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"http.server": "*"}


def _wrap_parse_request(
    wrapped: Callable[..., bool],
    instance: http_server.BaseHTTPRequestHandler,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> bool:
    parsed = wrapped(*args, **kwargs)
    if parsed and in_aws_lambda_microvm():
        core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (instance.command, instance.path))
    return parsed


def patch() -> None:
    if getattr(http_server, "__datadog_patch", False):
        return
    http_server.__datadog_patch = True  # type: ignore[attr-defined]  # patch marker, not a real module attr

    _w(http_server.BaseHTTPRequestHandler, "parse_request", _wrap_parse_request)


def unpatch() -> None:
    if not getattr(http_server, "__datadog_patch", False):
        return
    http_server.__datadog_patch = False  # type: ignore[attr-defined]  # patch marker, not a real module attr

    _u(http_server.BaseHTTPRequestHandler, "parse_request")
