"""Inactive Flask response hooks for the shared web blocking protocol.

Patching does not register this adapter; its caller must supply a normalized
response while Flask's request context is active and before transport commitment.
"""

from collections.abc import Mapping
from typing import Any
from typing import Optional
from typing import cast

from flask import current_app
from flask import request
from werkzeug.wrappers import Response

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.web_framework import ExecuteAdapter
from ddtrace.contrib.internal.web_framework import RequestInspectionDetails
from ddtrace.contrib.internal.web_framework import blocking_execute_adapter


def complete_response(event: WebFrameworkRequestEvent, result: Any, error: Optional[BaseException]) -> None:
    """Capture finalized response metadata without consuming or closing the body."""
    # Flask converts handled HTTP exceptions into responses before this boundary.
    # A failure without a response has no observed HTTP status; the owner handles error.
    event.response_from_exception = False
    event.response_status_code = result.status_code if isinstance(result, Response) else None
    # Preserve repeated headers in a detached Headers copy, never a flattened dict.
    # This metadata must not be used to reconstruct the application's wire response.
    event.response_headers = cast(Mapping[str, str], result.headers.copy()) if isinstance(result, Response) else {}


def _request_details(_kwargs: Mapping[str, Any]) -> RequestInspectionDetails:
    """Read matched arguments from Flask, not the dispatch method's keyword arguments."""
    return RequestInspectionDetails(
        peer_ip=request.environ.get("REMOTE_ADDR"),
        cookies=dict(request.cookies),
        path_params=dict(request.view_args or {}),
    )


def _is_response_object(outcome: Any) -> bool:
    return isinstance(outcome, Response)


def _response_close_target(outcome: Response) -> Response:
    # The shared adapter calls close() on the extracted object. Closing the Response
    # also runs call_on_close callbacks; returning response.response would skip them.
    return outcome


def _build_response(status: int, headers: Mapping[str, str], body: bytes) -> Response:
    return current_app.response_class(body, status=status, headers=list(headers.items()))


# Reuse the blocking protocol without creating another request context. The caller
# must supply a normalized Response before transport commitment, while Flask's
# request context is active. WSGI lifetime and hook registration remain separate.
execute_response: ExecuteAdapter = blocking_execute_adapter(
    request_details=_request_details,
    is_response_object=_is_response_object,
    extract_body=_response_close_target,
    build_response=_build_response,
)
