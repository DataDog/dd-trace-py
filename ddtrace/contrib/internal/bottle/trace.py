from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import MutableMapping
from typing import Any
from typing import Optional
from typing import cast

from bottle import BaseResponse
from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.contrib.internal.web_framework import RequestInspectionDetails
from ddtrace.contrib.internal.web_framework import blocking_execute_adapter
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)


class TracePlugin:
    name = "trace"
    api = 2

    def __init__(self, service="bottle", tracer=None, distributed_tracing=None):
        if tracer is not None:
            deprecate(
                "The tracer parameter is deprecated",
                message="The global tracer will be used instead.",
                category=DDTraceDeprecationWarning,
                removal_version="5.0.0",
            )
        self.service = config._get_service(default=service)
        if distributed_tracing is not None:
            config.bottle.distributed_tracing = distributed_tracing

    @property
    def distributed_tracing(self):
        return config.bottle.distributed_tracing

    @distributed_tracing.setter
    def distributed_tracing(self, distributed_tracing):
        config.bottle["distributed_tracing"] = asbool(distributed_tracing)

    def apply(self, callback: Callable[..., Any], route: Any) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not is_tracing_enabled():
                return callback(*args, **kwargs)

            method = request.method
            event = WebFrameworkRequestEvent(
                http_operation="bottle.request",
                service=self.service,
                resource=f"{method} {route.rule}",
                component=config.bottle.integration_name,
                request_headers=cast(MutableMapping[str, str], request.headers),
                request_url=request.url,
                query=request.query_string,
                request_route="/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")]),
                request_method=method,
                integration_config=config.bottle,
                headers_case_sensitive=True,
                activate_distributed_headers=True,
            )

            def invoke() -> Any:
                try:
                    result = callback(*args, **kwargs)
                except BaseException as error:
                    _complete_request(event, None, error)
                    raise
                _complete_request(event, result, None)
                return result

            with core.context_with_event(event):
                return _execute_request(event, invoke, kwargs, _complete_request)

        return wrapped


def _request_details(path_params: Mapping[str, Any]) -> RequestInspectionDetails:
    """Collect the request data subscribers receive in place of Bottle's request object."""
    return RequestInspectionDetails(
        peer_ip=request.environ.get("REMOTE_ADDR"),
        cookies=dict(request.cookies),
        path_params=path_params,
    )


def _is_http_response(outcome: Any) -> bool:
    return isinstance(outcome, HTTPResponse)


def _response_body(outcome: HTTPResponse) -> Any:
    return outcome.body


def _build_blocked_response(status: int, headers: Mapping[str, str], body: bytes) -> HTTPResponse:
    blocked = HTTPResponse(body=body, status=status, headers=headers)
    # Completion hooks must see the replacement headers, without the application's headers or cookies.
    blocked.apply(response)
    return blocked


_execute_request = blocking_execute_adapter(
    request_details=_request_details,
    is_response_object=_is_http_response,
    extract_body=_response_body,
    build_response=_build_blocked_response,
)


def _response_headers(response_data: BaseResponse) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in response_data.headers.allitems():
        headers[name] = f"{headers[name]}, {value}" if name in headers else value
    return headers


def _complete_request(event: WebFrameworkRequestEvent, result: Any, error: Optional[BaseException]) -> None:
    try:
        if isinstance(result, HTTPResponse):
            event.response_status_code = result.status_code
            event.response_headers = _response_headers(result)
            event.response_from_exception = False
        elif isinstance(error, HTTPResponse):
            event.response_status_code = error.status_code
            event.response_from_exception = True
            event.response_headers = _response_headers(error)
        else:
            event.response_status_code = 500 if isinstance(error, Exception) else response.status_code
            event.response_headers = _response_headers(response)
            event.response_from_exception = False
    except Exception:
        log.debug("Failed to collect Bottle response metadata", exc_info=True)
