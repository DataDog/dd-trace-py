"""Flask metadata and deferred request execution."""

from collections.abc import Callable
from collections.abc import Iterable
from contextvars import copy_context
import sys
from typing import Any

from flask import Request

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.contrib.internal.flask._streaming import FlaskResponseIterable
from ddtrace.internal import core
from ddtrace.internal.constants import FLASK_ENDPOINT
from ddtrace.internal.constants import FLASK_RESOURCE_FULL
from ddtrace.internal.constants import FLASK_URL_RULE
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.integration import IntegrationConfig


log = get_logger(__name__)


def prepare_request(environ: dict[str, Any], integration_config: IntegrationConfig) -> WebFrameworkRequestEvent:
    """Read metadata before Flask pushes its request context, without consuming input."""
    request = Request(environ)
    return WebFrameworkRequestEvent(
        http_operation="flask.request",
        component=integration_config.integration_name,
        integration_config=integration_config,
        service=trace_utils.int_service(None, integration_config),
        request_method=request.method,
        request_url=request.base_url,
        request_headers=dict(request.headers),
        query=environ.get("QUERY_STRING", ""),
        headers_case_sensitive=True,
        activate_distributed_headers=True,
        measured=False,
    )


def update_route(event: WebFrameworkRequestEvent, request: Request) -> None:
    """Keep the root event current while Flask's request proxy is available."""
    event.resource = None
    if request.endpoint:
        event.additional_tags[FLASK_ENDPOINT] = request.endpoint
        event.resource = f"{request.method} {request.endpoint}"
    if request.url_rule is not None and request.url_rule.rule:
        rule = request.url_rule.rule
        event.request_route = rule
        event.resource = f"{request.method} {rule}"
        event.additional_tags[FLASK_URL_RULE] = rule
        if request.script_root:
            event.additional_tags[FLASK_RESOURCE_FULL] = f"{request.method} {request.script_root}{rule}"


def record_response(event: WebFrameworkRequestEvent, status: str, headers: list[tuple[str, str]]) -> None:
    """Capture accepted headers without inspecting or consuming the response body."""
    event.response_status_code = int(status.partition(" ")[0])
    event.response_headers = dict(headers)


def run_flask_request(
    middleware: Any,
    environ: dict[str, Any],
    start_response: Callable[..., Any],
) -> Iterable[bytes]:
    trace_utils.dispatch_wsgi_web_request_starting(environ)
    try:
        event = prepare_request(environ, middleware._config)
    except Exception:
        log.debug("Failed to prepare Flask request metadata", exc_info=True)
        application: Callable[..., Iterable[bytes]] = middleware.app
        return application(environ, start_response)

    scope = core.context_with_event(event, context_name_override="flask.request", dispatch_end_event=False)
    # Existing child-span and security callbacks consume these aliases. In
    # particular, security processors read headers/IP synchronously at span start.
    scope.set_items(
        {
            "remote_addr": environ.get("REMOTE_ADDR"),
            "headers": event.request_headers,
            "headers_case_sensitive": True,
            "middleware": middleware,
            "middleware_config": middleware._config,
            "integration_config": middleware._config,
            "environ": environ,
            "flask_request_event": event,
            "span_key": "req_span",
        }
    )

    def accept_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        result = start_response(status, headers, exc_info)
        try:
            record_response(event, status, headers)
        except Exception:
            log.debug("Failed to record Flask response metadata", exc_info=True)
        return result

    execution_context = copy_context()

    def invoke() -> Iterable[bytes]:
        with scope:
            try:
                try:
                    response = middleware._execute_application(scope, accept_response)
                finally:
                    # Security cleanup still runs at application return; tracing
                    # completion is deferred until the response is exhausted/closed.
                    core.dispatch("context.ended.wsgi.__call__", (scope, sys.exc_info()))
            except BaseException:
                scope.dispatch_ended_event(*sys.exc_info())
                raise
            streamed_response = FlaskResponseIterable(response, scope, execution_context)
        return streamed_response

    return execution_context.run(invoke)
