import logging
from typing import Dict
from typing import Union

from ddtrace import Span
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT


log = logging.getLogger(__name__)

PROXY_HEADER_SYSTEM = "x-dd-proxy"
PROXY_HEADER_START_TIME_MS = "x-dd-proxy-request-time-ms"
PROXY_HEADER_PATH = "x-dd-proxy-path"
PROXY_HEADER_HTTPMETHOD = "x-dd-proxy-httpmethod"
PROXY_HEADER_DOMAIN = "x-dd-proxy-domain-name"
PROXY_HEADER_STAGE = "x-dd-proxy-stage"

supported_proxies: Dict[str, Dict[str, str]] = {
    "aws-apigateway": {"span_name": "aws.apigateway", "component": "aws-apigateway"}
}


def create_inferred_proxy_span_if_headers_exist(ctx, headers, child_of, tracer) -> None:
    if not headers:
        return None

    if not config._inferred_proxy_services_enabled:
        return None

    normalized_headers = normalize_headers(headers)

    proxy_context = extract_inferred_proxy_context(normalized_headers)

    if not proxy_context:
        return None

    proxy_span_info = supported_proxies[proxy_context["proxy_system_name"]]

    log.debug(
        "Successfully extracted inferred span info", proxy_context, " for proxy: ", proxy_context["proxy_system_name"]
    )

    span = tracer.start_span(
        proxy_span_info["span_name"],
        service=proxy_context.get("domain_name", config._get_service()),
        resource=proxy_span_info["span_name"],
        span_type=SpanTypes.WEB,
        activate=True,
        child_of=child_of,
    )
    span.start_ns = int(proxy_context["request_time"]) * 1000000

    log.debug("Successfully created inferred proxy span.")

    set_inferred_proxy_span_tags(span, proxy_context)

    # we need a callback to finish the api gateway span, this callback will be added to the child spans finish callbacks
    def finish_callback(_):
        span.finish()

    # headers = delete_inferred_header_keys(headers)

    if span:
        ctx.set_item("inferred_proxy_span", span)
        ctx.set_item("inferred_proxy_finish_callback", finish_callback)
        ctx.set_item("distributed_headers", headers)


def set_inferred_proxy_span_tags(span, proxy_context) -> Span:
    span.set_tag_str(COMPONENT, supported_proxies[proxy_context["proxy_system_name"]]["component"])
    span.set_tag_str(SPAN_KIND, SpanKind.INTERNAL)

    span.set_tag_str(http.METHOD, proxy_context["method"])
    span.set_tag_str(http.URL, f"{proxy_context['domain_name']}{proxy_context['path']}")
    span.set_tag_str(http.ROUTE, proxy_context["path"])
    span.set_tag_str("stage", proxy_context["stage"])

    span.set_tag_str("_dd.inferred_span", "1")
    return span


def extract_inferred_proxy_context(headers) -> Union[None, Dict[str, str]]:
    if PROXY_HEADER_START_TIME_MS not in headers:
        return None

    if not (PROXY_HEADER_SYSTEM in headers and headers[PROXY_HEADER_SYSTEM] in supported_proxies):
        log.debug(
            "Received headers to create inferred proxy span but headers include an unsupported proxy type", headers
        )
        return None

    return {
        "request_time": headers[PROXY_HEADER_START_TIME_MS] if headers[PROXY_HEADER_START_TIME_MS] else "0",
        "method": headers[PROXY_HEADER_HTTPMETHOD],
        "path": headers[PROXY_HEADER_PATH],
        "stage": headers[PROXY_HEADER_STAGE],
        "domain_name": headers[PROXY_HEADER_DOMAIN],
        "proxy_system_name": headers[PROXY_HEADER_SYSTEM],
    }


def normalize_headers(headers) -> Dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def delete_inferred_header_keys(headers) -> Dict[str, str]:
    keys_to_delete = [
        PROXY_HEADER_START_TIME_MS,
        PROXY_HEADER_HTTPMETHOD,
        PROXY_HEADER_PATH,
        PROXY_HEADER_STAGE,
        PROXY_HEADER_DOMAIN,
        PROXY_HEADER_SYSTEM,
    ]

    for key in keys_to_delete:
        if key in headers:
            del headers[key]

    return headers
