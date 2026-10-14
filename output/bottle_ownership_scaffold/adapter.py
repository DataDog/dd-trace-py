"""IDM: extract Bottle facts and enforce the synchronous decision."""

from collections.abc import Callable
from functools import wraps
from typing import Any
from typing import cast

from bottle import HTTPResponse
from bottle import request

from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core

from .contracts import BeforeHandlerEvent


class ScaffoldTracePlugin(TracePlugin):
    def apply(self, callback: Callable[..., Any], route: Any) -> Callable[..., Any]:
        @wraps(callback)
        def inspected(*args: Any, **kwargs: Any) -> Any:
            # The base wrapper bypasses its context when instrumentation is disabled.
            if not is_tracing_enabled():
                return callback(*args, **kwargs)

            event = BeforeHandlerEvent(
                operation_id=object(),
                http_operation="bottle.request",
                request_url=request.url,
                request_method=request.method,
                request_headers=dict(request.headers),
                query=request.query_string,
                route="/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")]),
                path_parameters=dict(kwargs),
            )
            # Strict prototype behavior: consumer failures surface, never silently allow.
            core.dispatch_event(event, allow_raise=True)
            decision = event.block_response
            if decision is not None:
                return HTTPResponse(body=decision.body, status=decision.status, headers=list(decision.headers))
            return callback(*args, **kwargs)

        # Preserve existing span creation, resource naming, exception and status handling.
        return cast(Callable[..., Any], super().apply(inspected, route))  # type: ignore[no-untyped-call]
