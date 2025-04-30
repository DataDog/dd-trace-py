import re
from typing import Any
from typing import Mapping
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.ext import http
from ddtrace.ext import user
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.http import normalize_header_name
from ddtrace.internal.utils.http import redact_url
from ddtrace.internal.utils.http import strip_query_string
from ddtrace.settings import IntegrationConfig
from ddtrace.settings._config import config
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

NORMALIZE_PATTERN = re.compile(r"([^a-z0-9_\-:/]){1}")


@cached()
def _normalized_header_name(header_name: str) -> str:
    return NORMALIZE_PATTERN.sub("_", normalize_header_name(header_name))


def _normalize_tag_name(request_or_response: str, header_name: str) -> str:
    """
    Given a tag name, e.g. 'Content-Type', returns a corresponding normalized tag name, i.e
    'http.request.headers.content_type'. Rules applied actual header name are:
    - any letter is converted to lowercase
    - any digit is left unchanged
    - any block of any length of different ASCII chars is converted to a single underscore '_'
    :param request_or_response: The context of the headers: request|response
    :param header_name: The header's name
    :type header_name: str
    :rtype: str
    """
    # Looking at:
    #   - http://www.iana.org/assignments/message-headers/message-headers.xhtml
    #   - https://tools.ietf.org/html/rfc6648
    # and for consistency with other language integrations seems safe to assume the following algorithm for header
    # names normalization:
    #   - any letter is converted to lowercase
    #   - any digit is left unchanged
    #   - any block of any length of different ASCII chars is converted to a single underscore '_'
    normalized_name = _normalized_header_name(header_name)
    return "http.{}.headers.{}".format(request_or_response, normalized_name)


def _get_header_value_case_insensitive(headers: Mapping[str, str], keyname: str) -> Optional[str]:
    """
    Get a header in a case insensitive way. This function is meant for frameworks
    like Django < 2.2 that don't store the headers in a case insensitive mapping.
    """
    # just in case we are lucky
    shortcut_value = headers.get(keyname)
    if shortcut_value is not None:
        return shortcut_value

    for key, value in headers.items():
        if key.lower().replace("_", "-") == keyname:
            return value

    return None


# Possible User Agent header.
USER_AGENT_PATTERNS = ("http-user-agent", "user-agent")


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


def set_user(
    tracer: Any,
    user_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    scope: Optional[str] = None,
    role: Optional[str] = None,
    session_id: Optional[str] = None,
    propagate: bool = False,
    span: Optional[Span] = None,
    may_block: bool = True,
    mode: str = "sdk",
):
    # type: (...) -> None
    """Set user tags.
    https://docs.datadoghq.com/logs/log_configuration/attributes_naming_convention/#user-related-attributes
    https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/?tab=set_tag&code-lang=python
    """
    if span is None:
        span = core.get_root_span()
    if span:
        if user_id:
            str_user_id = str(user_id)
            span.set_tag_str(user.ID, str_user_id)
            if propagate:
                span.context.dd_user_id = str_user_id

        # All other fields are optional
        if name:
            span.set_tag_str(user.NAME, name)
        if email:
            span.set_tag_str(user.EMAIL, email)
        if scope:
            span.set_tag_str(user.SCOPE, scope)
        if role:
            span.set_tag_str(user.ROLE, role)
        if session_id:
            span.set_tag_str(user.SESSION_ID, session_id)

        if (may_block or mode == "auto") and asm_config._asm_enabled:
            exc = core.dispatch_with_results("set_user_for_asm", [tracer, user_id, mode]).block_user.exception
            if exc:
                raise exc

    else:
        log.warning(
            "No root span in the current execution. Skipping set_user tags. "
            "See https://docs.datadoghq.com/security_platform/application_security/setup_and_configure/"
            "?tab=set_user&code-lang=python for more information.",
        )


def _set_url_tag(integration_config: IntegrationConfig, span: Span, url: str, query: str) -> None:
    if not integration_config.http_tag_query_string:
        span.set_tag_str(http.URL, strip_query_string(url))
    elif config._global_query_string_obfuscation_disabled:
        # TODO(munir): This case exists for backwards compatibility. To remove query strings from URLs,
        # users should set ``DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING=False``. This case should be
        # removed when config.global_query_string_obfuscation_disabled is removed (v3.0).
        span.set_tag_str(http.URL, url)
    elif getattr(config._obfuscation_query_string_pattern, "pattern", None) == b"":
        # obfuscation is disabled when DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP=""
        span.set_tag_str(http.URL, strip_query_string(url))
    else:
        span.set_tag_str(http.URL, redact_url(url, config._obfuscation_query_string_pattern, query))
