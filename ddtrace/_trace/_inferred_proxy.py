import logging
from typing import Dict
from typing import Union

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT
from ddtrace.propagation.http import _extract_header_value
from ddtrace.propagation.http import _possible_header


log = logging.getLogger(__name__)

# Checking lower case and upper case versions per WSGI spec following ddtrace/propagation/http.py's
# logic to extract http headers
POSSIBLE_PROXY_HEADER_SYSTEM = _possible_header("x-dd-proxy")
POSSIBLE_PROXY_HEADER_START_TIME_MS = _possible_header("x-dd-proxy-request-time-ms")
POSSIBLE_PROXY_HEADER_PATH = _possible_header("x-dd-proxy-path")
POSSIBLE_PROXY_HEADER_HTTPMETHOD = _possible_header("x-dd-proxy-httpmethod")
POSSIBLE_PROXY_HEADER_DOMAIN = _possible_header("x-dd-proxy-domain-name")
POSSIBLE_PROXY_HEADER_STAGE = _possible_header("x-dd-proxy-stage")

supported_proxies: Dict[str, Dict[str, str]] = {
    "aws-apigateway": {"span_name": "aws.apigateway", "component": "aws-apigateway"}
}


def create_inferred_proxy_span_if_headers_exist(ctx, headers, child_of, tracer) -> None:
    if not headers:
        return None

    normalized_headers = normalize_headers(headers)

    proxy_context = extract_inferred_proxy_context(normalized_headers)

    if not proxy_context:
        return None

    proxy_span_info = supported_proxies[proxy_context["proxy_system_name"]]

    span = tracer.start_span(
        proxy_span_info["span_name"],
        service=proxy_context.get("domain_name", config._get_service()),
        resource=proxy_context["method"] + " " + proxy_context["path"],
        span_type=SpanTypes.WEB,
        activate=True,
        child_of=child_of,
    )
    span.start_ns = int(proxy_context["request_time"]) * 1000000

    set_inferred_proxy_span_tags(span, proxy_context)

    # we need a callback to finish the api gateway span, this callback will be added to the child spans finish callbacks
    def finish_callback(_):
        span.finish()

    if span:
        ctx.set_item("inferred_proxy_span", span)
        ctx.set_item("inferred_proxy_finish_callback", finish_callback)
        ctx.set_item("headers", headers)


def set_inferred_proxy_span_tags(span, proxy_context) -> Span:
    span.set_tag_str(COMPONENT, supported_proxies[proxy_context["proxy_system_name"]]["component"])

    span.set_tag_str(http.METHOD, proxy_context["method"])
    span.set_tag_str(http.URL, f"{proxy_context['domain_name']}{proxy_context['path']}")
    span.set_tag_str("stage", proxy_context["stage"])

    span.set_metric("_dd.inferred_span", 1)
    return span


def extract_inferred_proxy_context(headers) -> Union[None, Dict[str, str]]:
    proxy_header_system = str(_extract_header_value(POSSIBLE_PROXY_HEADER_SYSTEM, headers))
    proxy_header_start_time_ms = str(_extract_header_value(POSSIBLE_PROXY_HEADER_START_TIME_MS, headers))
    proxy_header_path = str(_extract_header_value(POSSIBLE_PROXY_HEADER_PATH, headers))
    proxy_header_httpmethod = str(_extract_header_value(POSSIBLE_PROXY_HEADER_HTTPMETHOD, headers))
    proxy_header_domain = str(_extract_header_value(POSSIBLE_PROXY_HEADER_DOMAIN, headers))
    proxy_header_stage = str(_extract_header_value(POSSIBLE_PROXY_HEADER_STAGE, headers))

    # Exit if start time header is not present
    if proxy_header_start_time_ms is None:
        return None

    # Exit if proxy header system name is not present or is a system we don't support
    if not (proxy_header_system and proxy_header_system in supported_proxies):
        log.debug(
            "Received headers to create inferred proxy span but headers include an unsupported proxy type", headers
        )
        return None

    return {
        "request_time": proxy_header_start_time_ms,
        "method": proxy_header_httpmethod,
        "path": proxy_header_path,
        "stage": proxy_header_stage,
        "domain_name": proxy_header_domain,
        "proxy_system_name": proxy_header_system,
    }


def normalize_headers(headers) -> Dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}
