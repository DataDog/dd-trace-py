from collections.abc import Callable
from collections.abc import MutableMapping
from typing import Any
from typing import cast

from bottle import HTTPError
from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
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
            if not is_tracing_enabled():
                return callback(*args, **kwargs)

            event = WebFrameworkRequestEvent(
                http_operation="bottle.request",
                service=self.service,
                resource_route=route.rule,
                component=config.bottle.integration_name,
                request_headers=cast(MutableMapping[str, str], request.headers),
                request_url=request.url,
                query=request.query_string,
                request_route="/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")]),
                request_method=request.method,
                integration_config=config.bottle,
                headers_case_sensitive=True,
                activate_distributed_headers=True,
            )
            with core.context_with_event(event):
                code = None
                result = None
                try:
                    result = callback(*args, **kwargs)
                    return result
                except (HTTPError, HTTPResponse) as e:
                    # you can interrupt flows using abort(status_code, 'message')...
                    # we need to respect the defined status_code.
                    # we also need to handle when response is raised as is the
                    # case with a 4xx status
                    code = e.status_code
                    raise
                except Exception:
                    # bottle doesn't always translate unhandled exceptions, so
                    # we mark it here.
                    code = 500
                    raise
                finally:
                    # Bottle's local response carries headers even when a returned
                    # or raised HTTPResponse overrides the status.
                    event.set_response(response)
                    if isinstance(result, HTTPResponse):
                        event.response_status_code = result.status_code
                    elif code:
                        event.response_status_code = code

        return wrapped
