from typing import Any
from typing import Optional

from ddtrace._trace.http_meta import NORMALIZE_PATTERN  # noqa:F401
from ddtrace._trace.http_meta import SECURITY_TESTING_HEADERS  # noqa:F401
from ddtrace._trace.http_meta import USER_AGENT_PATTERNS  # noqa:F401
from ddtrace._trace.http_meta import _get_header_value_case_insensitive  # noqa:F401
from ddtrace._trace.http_meta import _get_request_header_user_agent  # noqa:F401
from ddtrace._trace.http_meta import _normalize_tag_name  # noqa:F401
from ddtrace._trace.http_meta import _normalized_header_name  # noqa:F401
from ddtrace._trace.http_meta import _set_url_tag  # noqa:F401
from ddtrace._trace.http_meta import _store_security_testing_headers  # noqa:F401

# Re-exported because products such as AppSec and AI Guard reach the tracing helpers through the contrib
# layer instead of importing ddtrace._trace directly.
from ddtrace._trace.http_tags import http_block_metadata  # noqa: F401
from ddtrace._trace.http_tags import name_blocked_request_span  # noqa: F401
from ddtrace._trace.http_tags import set_client_address_tags  # noqa: F401
from ddtrace._trace.http_tags import set_url_tags_server  # noqa: F401
from ddtrace._trace.otel.http.tags import _sanitized_url  # noqa:F401
from ddtrace.ext import user
from ddtrace.internal import core
from ddtrace.internal import span_bus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)


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
