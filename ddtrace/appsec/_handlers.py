from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.span import Span
from ddtrace.appsec._api_security._normalized_route import normalize_route
from ddtrace.appsec._api_security._normalized_route import normalize_route_django
from ddtrace.appsec._constants import API_SECURITY
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.internal import core
from ddtrace.internal import telemetry
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


logger = get_logger(__name__)


# set_http_meta


def _on_set_http_meta(
    span: Span,
    request_ip: Optional[str],
    raw_uri: Optional[str],
    route: Optional[str],
    method: Optional[str],
    request_headers: Optional[Mapping[str, Optional[str]]],
    request_cookies: Optional[dict[str, str]],
    parsed_query: Optional[Mapping[str, Any]],
    request_path_params: Optional[Union[Mapping[str, Any], Sequence[Any]]],
    request_body: Any,
    status_code: Optional[Union[int, str]],
    response_headers: Optional[Mapping[str, Optional[str]]],
    response_cookies: Optional[Mapping[str, str]],
    peer_ip: Optional[str] = None,
    headers_are_case_sensitive: bool = False,
) -> None:
    if asm_config._asm_enabled and span.span_type in asm_config._asm_http_span_types:
        # avoid circular import
        from ddtrace.appsec._asm_request_context import set_waf_address

        status_code = str(status_code) if status_code is not None else None

        addresses = [
            (SPAN_DATA_NAMES.REQUEST_HTTP_IP, request_ip),
            (SPAN_DATA_NAMES.REQUEST_URI_RAW, raw_uri),
            (SPAN_DATA_NAMES.REQUEST_ROUTE, route),
            (SPAN_DATA_NAMES.REQUEST_METHOD, method),
            (SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, request_headers),
            (SPAN_DATA_NAMES.REQUEST_COOKIES, request_cookies),
            (SPAN_DATA_NAMES.REQUEST_QUERY, parsed_query),
            (SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, request_path_params),
            (SPAN_DATA_NAMES.REQUEST_BODY, request_body),
            (SPAN_DATA_NAMES.RESPONSE_STATUS, status_code),
            (SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, response_headers),
        ]
        for k, v in addresses:
            if v is not None:
                set_waf_address(k, v)


# Per-integration normalizer dispatch. Adding a new framework here is enough to opt it into RFC-1103 emission, provided
# its grammar-specific normalizer exists.
_NORMALIZED_ROUTE_BY_INTEGRATION = {
    "starlette": normalize_route,
    "fastapi": normalize_route,
    "django": normalize_route_django,
}


def _on_set_http_meta_for_normalized_route(
    span: Span,
    request_ip: Optional[str],
    raw_uri: Optional[str],
    route: Optional[str],
    method: Optional[str],
    request_headers: Optional[Mapping[str, Optional[str]]],
    request_cookies: Optional[dict[str, str]],
    parsed_query: Optional[Mapping[str, Any]],
    request_path_params: Optional[Union[Mapping[str, Any], Sequence[Any]]],
    request_body: Any,
    status_code: Optional[Union[int, str]],
    response_headers: Optional[Mapping[str, Optional[str]]],
    response_cookies: Optional[Mapping[str, str]],
    peer_ip: Optional[str] = None,
    headers_are_case_sensitive: bool = False,
) -> None:
    # RFC-1103: emit `_dd.appsec.normalized_route` on supported web framework request spans when API Security is
    # active. The framework is identified via the IntegrationConfig every web integration places in the request
    # execution context (see e.g. asgi/middleware.py:258) — survives `request_span_name` overrides and the schema-v1
    # rename to "http.server.request". Each framework has its own URL grammar; the dispatch picks the matching
    # normalizer. Other frameworks aren't normalized until they get their own grammar-aware implementation.
    if not asm_config._api_security_feature_active:
        return
    if not route:
        return
    # No active ASM context → appsec/api-sec inactive for this request, nothing to do. The flag also gives us
    # idempotency for repeat dispatches (Django's sync path fires twice).
    from ddtrace.appsec._asm_request_context import get_active_asm_context

    asm_env = get_active_asm_context()
    if asm_env is None or asm_env.normalized_route_emitted:
        return
    integration_config = core.find_item("integration_config")
    integration_name = getattr(integration_config, "integration_name", None)
    if not isinstance(integration_name, str):
        return
    normalizer = _NORMALIZED_ROUTE_BY_INTEGRATION.get(integration_name)
    if normalizer is None:
        return
    normalized = normalizer(route, request_path_params)
    if normalized is not None:
        span._set_attribute(API_SECURITY.NORMALIZED_ROUTE, normalized)
        asm_env.normalized_route_emitted = True


def _on_telemetry_periodic() -> None:
    try:
        telemetry.telemetry_writer.add_configuration(
            APPSEC.ENV,
            int(asm_config._asm_enabled),
            asm_config.asm_enabled_origin,
        )
    except Exception:
        logger.debug("Could not set appsec_enabled telemetry config status", exc_info=True)


def listen() -> None:
    core.on("telemetry.periodic", _on_telemetry_periodic)

    core.on("set_http_meta_for_asm", _on_set_http_meta)
    core.on("set_http_meta_for_asm", _on_set_http_meta_for_normalized_route)
