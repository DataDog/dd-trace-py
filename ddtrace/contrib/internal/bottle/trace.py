from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import MutableMapping
from functools import partial
from typing import Any
from typing import Optional
from typing import cast

from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.web_framework import CompleteCallback
from ddtrace.contrib.internal.web_framework import RequestInspectionDetails
from ddtrace.contrib.internal.web_framework import blocking_execute_adapter
from ddtrace.contrib.internal.web_framework import run_web_framework_request
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.formats import asbool


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
        prepare = partial(_prepare_request, self, route)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            return _run_request(callback, args, kwargs, prepare=prepare, complete=_complete_request)

        return wrapped


def _run_request(
    callback: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    prepare: Callable[[], WebFrameworkRequestEvent],
    complete: CompleteCallback,
) -> Any:
    return run_web_framework_request(
        callback,
        args,
        kwargs,
        prepare=prepare,
        complete=complete,
        context_name_override="bottle.request",
        execute=_execute_request,
    )


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


def _prepare_request(plugin: TracePlugin, route: Any) -> WebFrameworkRequestEvent:
    method = request.method
    full_route = "/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")])
    return WebFrameworkRequestEvent(
        http_operation="bottle.request",
        service=plugin.service,
        resource=f"{method} {route.rule}",
        component=config.bottle.integration_name,
        request_headers=cast(MutableMapping[str, str], request.headers),
        request_url=request.url,
        query=request.query_string,
        request_route=full_route,
        request_method=method,
        integration_config=config.bottle,
        headers_case_sensitive=True,
        activate_distributed_headers=True,
    )


def _complete_request(event: WebFrameworkRequestEvent, result: Any, error: Optional[BaseException]) -> None:
    if isinstance(result, HTTPResponse):
        response_data = result
    elif isinstance(error, HTTPResponse):
        response_data = error
    else:
        response_data = response
    event.response_status_code = (
        500 if isinstance(error, Exception) and not isinstance(error, HTTPResponse) else response_data.status_code
    )
    event.response_headers = response_data.headers
    event.response_from_exception = isinstance(error, HTTPResponse)
