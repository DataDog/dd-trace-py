"""Flask request, route, and response metadata extraction."""

from typing import Any

from flask import Request

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.internal.constants import FLASK_ENDPOINT
from ddtrace.internal.constants import FLASK_RESOURCE_FULL
from ddtrace.internal.constants import FLASK_URL_RULE
from ddtrace.internal.settings.integration import IntegrationConfig


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
