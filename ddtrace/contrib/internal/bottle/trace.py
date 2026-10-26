from collections.abc import Callable
from collections.abc import MutableMapping
from functools import partial
from typing import Any
from typing import Optional
from typing import cast

from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.bottle import BottleBlockResponseEvent
from ddtrace.contrib._events.bottle import BottleRequestStartEvent
from ddtrace.contrib._events.bottle import BottleResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.web_framework import complete_request_safely
from ddtrace.contrib.internal.web_framework import run_web_framework_request
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal._exceptions import find_exception
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import Block_config
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.http import _format_template


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
    complete: Callable[[WebFrameworkRequestEvent, Any, Optional[BaseException]], None],
) -> Any:
    return run_web_framework_request(
        callback,
        args,
        kwargs,
        prepare=prepare,
        complete=complete,
        context_name_override="bottle.request",
        execute=partial(_execute_request, path_params=kwargs, complete=complete),
    )


def _execute_request(
    event: WebFrameworkRequestEvent,
    invoke: Callable[[], Any],
    *,
    path_params: dict[str, Any],
    complete: Callable[[WebFrameworkRequestEvent, Any, Optional[BaseException]], None],
) -> Any:
    """Inspect and replace Bottle outcomes while the shared request context is active."""
    result = None
    error: Optional[BaseException] = None
    blocking: Optional[BlockingException] = None
    try:
        try:
            try:
                # WAF evaluation can block, so it must run inside the handler rather than context entry.
                core.dispatch_event(BottleRequestStartEvent(event, request, path_params))
            except BaseException as exc:
                # The shared invocation was skipped, so it cannot complete this outcome.
                complete_request_safely(complete, event, None, exc)
                raise
            result = invoke()
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            core.dispatch_event(BottleResponseEvent(event))
    except BaseException as exc:
        blocking = find_exception(exc, BlockingException)
        if blocking is None:
            raise
        if isinstance(result, HTTPResponse):
            discarded_body = result.body
        elif isinstance(error, HTTPResponse):
            discarded_body = error.body
        else:
            discarded_body = result
        close = getattr(discarded_body, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                log.debug("Failed to close blocked Bottle response", exc_info=True)
        result = _blocked_response(blocking.args[0])
        complete_request_safely(complete, event, result, None)
        return result
    finally:
        # Do not retain the application's traceback through this frame.
        error = None
        blocking = None


def _blocked_response(block: Block_config) -> HTTPResponse:
    event = BottleBlockResponseEvent(block)
    core.dispatch_event(event)
    block_response = event.response
    if block_response is None:
        body = _format_template(BLOCKED_RESPONSE_JSON, "default").encode("utf-8")
        block_response = (403, {"Content-Type": "application/json", "Content-Length": str(len(body))}, body)
    status, headers, body = block_response
    blocked = HTTPResponse(body=body, status=status, headers=headers)
    # Completion hooks must see the replacement headers, without the application's headers or cookies.
    blocked.apply(response)
    return blocked


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
