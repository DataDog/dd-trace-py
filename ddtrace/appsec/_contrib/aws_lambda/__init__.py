from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.span import Span
from ddtrace.appsec._asm_request_context import _call_waf
from ddtrace.appsec._asm_request_context import _call_waf_first
from ddtrace.appsec._asm_request_context import set_body_response
from ddtrace.appsec._handlers import _on_set_http_meta
from ddtrace.appsec._http_utils import extract_cookies_from_headers
from ddtrace.appsec._http_utils import normalize_headers
from ddtrace.appsec._http_utils import parse_http_body
from ddtrace.internal import core
from ddtrace.internal.settings.asm import config as asm_config


def _on_lambda_start_request(
    span: Span,
    request_headers: dict[str, str],
    request_ip: Optional[str],
    body: Optional[str],
    is_body_base64: bool,
    raw_uri: str,
    route: str,
    method: str,
    parsed_query: dict[str, Any],
    request_path_parameters: Optional[dict[str, Any]],
) -> None:
    if not (asm_config._asm_enabled and span.span_type in asm_config._asm_http_span_types):
        return

    headers = normalize_headers(request_headers)
    request_body = parse_http_body(headers, body, is_body_base64)
    request_cookies = extract_cookies_from_headers(headers)

    _on_set_http_meta(
        span,
        request_ip,
        raw_uri,
        route,
        method,
        headers,
        request_cookies,
        parsed_query,
        request_path_parameters,
        request_body,
        None,
        None,
        None,
    )

    _call_waf_first(("aws_lambda",))


def _on_lambda_start_response(
    span: Span,
    status_code: str,
    response_headers: dict[str, str],
) -> None:
    if not (asm_config._asm_enabled and span.span_type in asm_config._asm_http_span_types):
        return

    waf_headers = normalize_headers(response_headers)
    response_cookies = extract_cookies_from_headers(waf_headers)

    _on_set_http_meta(
        span,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        status_code,
        waf_headers,
        response_cookies,
    )

    _call_waf(("aws_lambda",))


def _on_lambda_parse_body(
    response_body: Optional[Union[str, dict[str, Any]]],
) -> None:
    if asm_config._api_security_feature_active and response_body:
        set_body_response(response_body)


def listen() -> None:
    core.on("aws_lambda.start_request", _on_lambda_start_request)
    core.on("aws_lambda.start_response", _on_lambda_start_response)
    core.on("aws_lambda.parse_body", _on_lambda_parse_body)
