from collections.abc import Callable
from collections.abc import MutableMapping
from contextlib import nullcontext
from typing import Any
from typing import Optional
from typing import cast

from bottle import HTTPError
from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework_callback import WebFrameworkCallbackCompletedEvent
from ddtrace.contrib._events.web_framework_callback import WebFrameworkCallbackStartedEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core
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
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            tracing = is_tracing_enabled()
            observe = core.has_listeners(WebFrameworkCallbackStartedEvent.event_name) or core.has_listeners(
                WebFrameworkCallbackCompletedEvent.event_name
            )
            if not tracing and not observe:
                return callback(*args, **kwargs)

            resource = f"{request.method} {route.rule}"

            method = request.method
            url = request.url
            full_route = "/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")])

            event: Optional[WebFrameworkRequestEvent] = None
            if tracing:
                event = WebFrameworkRequestEvent(
                    http_operation="bottle.request",
                    service=self.service,
                    resource=resource,
                    component=config.bottle.integration_name,
                    request_headers=cast(MutableMapping[str, str], request.headers),
                    request_url=url,
                    query=request.query_string,
                    request_route=full_route,
                    request_method=method,
                    integration_config=config.bottle,
                    headers_case_sensitive=True,
                    activate_distributed_headers=True,
                )
            # Keep owned values separate from the event envelopes listeners receive.
            fields: Optional[dict[str, Any]] = (
                dict(
                    callback_id=object(),
                    framework="bottle",
                    request_method=method,
                    request_url=url,
                    request_route=full_route,
                    request_headers=dict(request.headers.items()),
                    query=request.query_string,
                )
                if observe
                else None
            )
            with core.context_with_event(event) if event is not None else nullcontext():
                code = None
                result = None
                raised_response = None
                error_type = None
                error_message = None
                try:
                    if fields is not None:
                        core.dispatch_event(WebFrameworkCallbackStartedEvent(**fields))
                    result = callback(*args, **kwargs)
                    return result
                except (HTTPError, HTTPResponse) as e:
                    # you can interrupt flows using abort(status_code, 'message')...
                    # we need to respect the defined status_code.
                    # we also need to handle when response is raised as is the
                    # case with a 4xx status
                    code = e.status_code
                    raised_response = e
                    raise
                except BaseException as error:
                    # bottle doesn't always translate unhandled exceptions, so
                    # we mark it here.
                    if isinstance(error, Exception):
                        code = 500
                    if fields is not None:
                        error_type = f"{type(error).__module__}.{type(error).__qualname__}"
                        try:
                            error_message = str(error)
                        except Exception:
                            error_message = None
                    raise
                finally:
                    if isinstance(result, HTTPResponse):
                        response_code = result.status_code
                    elif code:
                        response_code = code
                    else:
                        # bottle local response has not yet been updated so this
                        # will be default
                        response_code = response.status_code

                    if event is not None:
                        event.response_status_code = response_code
                        event.response_headers = response.headers
                    if fields is not None:
                        callback_response = result if isinstance(result, HTTPResponse) else raised_response
                        headers = callback_response.headerlist if callback_response is not None else response.headerlist
                        core.dispatch_event(
                            WebFrameworkCallbackCompletedEvent(
                                **fields,
                                response_status_code=response_code,
                                response_header_items=tuple(headers),
                                error_type=error_type,
                                error_message=error_message,
                            )
                        )

        return wrapped
