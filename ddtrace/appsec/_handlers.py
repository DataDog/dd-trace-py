from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.internal import core
from ddtrace.internal import telemetry
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


logger = get_logger(__name__)


# set_http_meta


def _on_set_http_meta(
    span,
    request_ip,
    raw_uri,
    route,
    method,
    request_headers,
    request_cookies,
    parsed_query,
    request_path_params,
    request_body,
    status_code,
    response_headers,
    response_cookies,
):
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


def _on_telemetry_periodic():
    try:
        telemetry.telemetry_writer.add_configurations(
            [
                (
                    APPSEC.ENV,
                    int(asm_config._asm_enabled),
                    asm_config.asm_enabled_origin,
                )
            ]
        )
    except Exception:
        logger.debug("Could not set appsec_enabled telemetry config status", exc_info=True)


def listen():
    core.on("telemetry.periodic", _on_telemetry_periodic)

    core.on("set_http_meta_for_asm", _on_set_http_meta)
