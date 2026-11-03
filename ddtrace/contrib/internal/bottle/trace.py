from collections.abc import Callable
from collections.abc import MutableMapping
from typing import Any
from typing import Optional
from typing import cast

from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
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
            event: Optional[WebFrameworkRequestEvent]
            try:
                event = _prepare_request(self, route)
            except Exception:
                log.debug("Failed to prepare Bottle request metadata", exc_info=True)
                event = None
            if event is None:
                return callback(*args, **kwargs)

            with core.context_with_event(event):
                result = None
                error: Optional[BaseException] = None
                try:
                    result = callback(*args, **kwargs)
                    return result
                except BaseException as exc:
                    error = exc
                    raise
                finally:
                    try:
                        _complete_request(event, result, error)
                    except Exception:
                        log.debug("Failed to complete Bottle request metadata", exc_info=True)
                    finally:
                        # Do not retain the application's traceback through this frame.
                        error = None

        return wrapped


def _prepare_request(plugin: TracePlugin, route: Any) -> WebFrameworkRequestEvent:
    method = request.method
    full_route = "/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")])
    return WebFrameworkRequestEvent(
        http_operation="bottle.request",
        tracing_enabled=is_tracing_enabled(),
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
