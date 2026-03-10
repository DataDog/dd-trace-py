from collections.abc import Mapping
from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.span import Span
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
    request_path_params: Optional[Mapping[str, Any]],
    request_body: Any,
    status_code: Optional[Union[int, str]],
    response_headers: Optional[Mapping[str, Optional[str]]],
    response_cookies: Optional[Mapping[str, str]],
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
