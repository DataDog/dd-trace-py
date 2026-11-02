from collections.abc import Mapping
from typing import Any
from typing import Optional

# Re-exported because products such as AppSec and AI Guard reach the tracing helpers through the contrib
# layer instead of importing ddtrace._trace directly.
from ddtrace._trace.http_tags import http_block_metadata  # noqa: F401
from ddtrace._trace.http_tags import name_blocked_request_span  # noqa: F401
from ddtrace._trace.http_tags import set_client_address_tags  # noqa: F401
from ddtrace._trace.http_tags import set_url_tags_server  # noqa: F401
from ddtrace._trace.otel.http.tags import _sanitized_url
from ddtrace._trace.span import Span
from ddtrace.ext import http
from ddtrace.ext import user
from ddtrace.internal import core
from ddtrace.internal import span_bus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils.http import NORMALIZE_PATTERN as NORMALIZE_PATTERN
from ddtrace.internal.utils.http import _get_header_value_case_insensitive as _get_header_value_case_insensitive
from ddtrace.internal.utils.http import _normalize_tag_name as _normalize_tag_name
from ddtrace.internal.utils.http import _normalized_header_name as _normalized_header_name


log = get_logger(__name__)

# Possible User Agent header.
USER_AGENT_PATTERNS = ("http-user-agent", "user-agent")

# Datadog scan/test markers, tagged unconditionally so the API endpoint
# reducer can keep scan/test traffic out of the API inventory.
SECURITY_TESTING_HEADERS = ("x-datadog-endpoint-scan", "x-datadog-security-test")


def _get_request_header_user_agent(headers: Mapping[str, str], headers_are_case_sensitive: bool = False) -> str:
    """Get user agent from request headers
    :param headers: A dict of http headers to be stored in the span
    :type headers: dict or list
    """
    for key_pattern in USER_AGENT_PATTERNS:
        if not headers_are_case_sensitive:
            user_agent = headers.get(key_pattern)
        else:
            user_agent = _get_header_value_case_insensitive(headers, key_pattern)

        if user_agent:
            return user_agent
    return ""


def _store_security_testing_headers(
    headers: Mapping[str, str], span: Span, headers_are_case_sensitive: bool = False
) -> None:
    """Tag SECURITY_TESTING_HEADERS on the span, regardless of integration config."""
    for header_name in SECURITY_TESTING_HEADERS:
        if not headers_are_case_sensitive:
            value = headers.get(header_name)
        else:
            value = _get_header_value_case_insensitive(headers, header_name)
        if value is not None:
            span._set_attribute(_normalize_tag_name("request", header_name), value)


def set_user(
    tracer: Any,
    user_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    scope: Optional[str] = None,
    role: Optional[str] = None,
    session_id: Optional[str] = None,
    propagate: bool = False,
    span: Optional[SpanData] = None,
    may_block: bool = True,
    mode: str = "sdk",
) -> None:
    """Set user tags.
    https://docs.datadoghq.com/logs/log_configuration/attributes_naming_convention/#user-related-attributes
    https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/?tab=set_tag&code-lang=python
    """
    if span is None:
        span = span_bus.get_root_span()
    if span:
        if user_id:
            str_user_id = str(user_id)
            span._set_attribute(user.ID, str_user_id)
            if propagate:
                span.context.dd_user_id = str_user_id

        # All other fields are optional
        if name:
            span._set_attribute(user.NAME, name)
        if email:
            span._set_attribute(user.EMAIL, email)
        if scope:
            span._set_attribute(user.SCOPE, scope)
        if role:
            span._set_attribute(user.ROLE, role)
        if session_id:
            span._set_attribute(user.SESSION_ID, session_id)

        if (may_block or mode == "auto") and asm_config._asm_enabled:
            exc = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
                "set_user_for_asm", [user_id, mode, session_id]
            ).block_user.exception
            if exc:
                raise exc

    else:
        log.warning(
            "No root span in the current execution. Skipping set_user tags. "
            "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )


def _set_url_tag(integration_config: IntegrationConfig, span: SpanData, url: str, query: str) -> None:
    span._set_attribute(http.URL, _sanitized_url(url, query, integration_config.http_tag_query_string))
