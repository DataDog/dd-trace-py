from dataclasses import dataclass
import logging
from typing import Callable
from typing import Dict
from typing import Optional

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT
from ddtrace.propagation.http import _extract_header_value
from ddtrace.propagation.http import _possible_header


log = logging.getLogger(__name__)


@dataclass
class ProxyHeaderContext:
    system_name: str
    request_time: str
    method: Optional[str]
    path: Optional[str]
    resource_path: Optional[str]
    domain_name: Optional[str]
    stage: Optional[str]
    account_id: Optional[str]
    api_id: Optional[str]
    region: Optional[str]
    user: Optional[str]
    useragent: Optional[str]


@dataclass
class ProxyInfo:
    span_name: str
    component: str
    resource_arn_builder: Optional[Callable[[ProxyHeaderContext], Optional[str]]] = None


def _api_gateway_rest_api_arn(proxy_context: ProxyHeaderContext) -> Optional[str]:
    if proxy_context.region and proxy_context.api_id:
        return f"arn:aws:apigateway:{proxy_context.region}::/restapis/{proxy_context.api_id}"
    return None


def _api_gateway_http_api_arn(proxy_context: ProxyHeaderContext) -> Optional[str]:
    if proxy_context.region and proxy_context.api_id:
        return f"arn:aws:apigateway:{proxy_context.region}::/apis/{proxy_context.api_id}"
    return None


supported_proxies: Dict[str, ProxyInfo] = {
    "aws-apigateway": ProxyInfo("aws.apigateway", "aws-apigateway", _api_gateway_rest_api_arn),
    "aws-httpapi": ProxyInfo("aws.httpapi", "aws-httpapi", _api_gateway_http_api_arn),
}

SUPPORTED_PROXY_SPAN_NAMES = {info.span_name for info in supported_proxies.values()}

# Checking lower case and upper case versions per WSGI spec following ddtrace/propagation/http.py's
# logic to extract http headers
POSSIBLE_PROXY_HEADER_SYSTEM = _possible_header("x-dd-proxy")
POSSIBLE_PROXY_HEADER_START_TIME_MS = _possible_header("x-dd-proxy-request-time-ms")
POSSIBLE_PROXY_HEADER_PATH = _possible_header("x-dd-proxy-path")
POSSIBLE_PROXY_HEADER_RESOURCE_PATH = _possible_header("x-dd-proxy-resource-path")
POSSIBLE_PROXY_HEADER_HTTPMETHOD = _possible_header("x-dd-proxy-httpmethod")
POSSIBLE_PROXY_HEADER_DOMAIN = _possible_header("x-dd-proxy-domain-name")
POSSIBLE_PROXY_HEADER_STAGE = _possible_header("x-dd-proxy-stage")
POSSIBLE_PROXY_HEADER_ACCOUNT_ID = _possible_header("x-dd-proxy-account-id")
POSSIBLE_PROXY_HEADER_API_ID = _possible_header("x-dd-proxy-api-id")
POSSIBLE_PROXY_HEADER_REGION = _possible_header("x-dd-proxy-region")
POSSIBLE_PROXY_HEADER_USER = _possible_header("x-dd-proxy-user")

HEADER_USERAGENT = _possible_header("user-agent")


def create_inferred_proxy_span_if_headers_exist(ctx, headers, child_of, tracer) -> None:
    if not headers:
        return None

    normalized_headers = normalize_headers(headers)

    proxy_context = extract_inferred_proxy_context(normalized_headers)

    if not proxy_context:
        return None

    proxy_info = supported_proxies[proxy_context.system_name]

    method = proxy_context.method
    route_or_path = proxy_context.resource_path or proxy_context.path
    resource = f"{method or ''} {route_or_path or ''}"

    span = tracer.start_span(
        proxy_info.span_name,
        service=proxy_context.domain_name or config._get_service(),
        resource=resource,
        span_type=SpanTypes.WEB,
        activate=True,
        child_of=child_of,
    )
    span.start_ns = int(proxy_context.request_time) * 1000000

    set_inferred_proxy_span_tags(span, proxy_context, proxy_info)

    # we need a callback to finish the api gateway span, this callback will be added to the child spans finish callbacks
    def finish_callback(_):
        span.finish()

    if span:
        ctx.set_item("inferred_proxy_span", span)
        ctx.set_item("inferred_proxy_finish_callback", finish_callback)
        ctx.set_item("headers", headers)


def set_inferred_proxy_span_tags(span: Span, proxy_context: ProxyHeaderContext, proxy_info: ProxyInfo) -> Span:
    span._set_tag_str(COMPONENT, proxy_info.component)
    span._set_tag_str("span.kind", SpanKind.SERVER)

    span._set_tag_str(http.URL, f"https://{proxy_context.domain_name or ''}{proxy_context.path or ''}")

    if proxy_context.method:
        span._set_tag_str(http.METHOD, proxy_context.method)

    if proxy_context.resource_path:
        span._set_tag_str(http.ROUTE, proxy_context.resource_path)

    if proxy_context.useragent:
        span._set_tag_str(http.USER_AGENT, proxy_context.useragent)

    if proxy_context.stage:
        span._set_tag_str("stage", proxy_context.stage)

    if proxy_context.account_id:
        span._set_tag_str("account_id", proxy_context.account_id)

    if proxy_context.api_id:
        span._set_tag_str("apiid", proxy_context.api_id)

    if proxy_context.region:
        span._set_tag_str("region", proxy_context.region)

    if proxy_context.user:
        span._set_tag_str("aws_user", proxy_context.user)

    if proxy_info.resource_arn_builder:
        resource_arn = proxy_info.resource_arn_builder(proxy_context)
        if resource_arn:
            span._set_tag_str("dd_resource_key", resource_arn)

    span.set_metric("_dd.inferred_span", 1)
    return span


def extract_inferred_proxy_context(headers) -> Optional[ProxyHeaderContext]:
    proxy_header_system = _extract_header_value(POSSIBLE_PROXY_HEADER_SYSTEM, headers)
    proxy_header_start_time_ms = _extract_header_value(POSSIBLE_PROXY_HEADER_START_TIME_MS, headers)
    proxy_header_path = _extract_header_value(POSSIBLE_PROXY_HEADER_PATH, headers)
    proxy_header_resource_path = _extract_header_value(POSSIBLE_PROXY_HEADER_RESOURCE_PATH, headers)

    proxy_header_httpmethod = _extract_header_value(POSSIBLE_PROXY_HEADER_HTTPMETHOD, headers)
    proxy_header_domain = _extract_header_value(POSSIBLE_PROXY_HEADER_DOMAIN, headers)
    proxy_header_stage = _extract_header_value(POSSIBLE_PROXY_HEADER_STAGE, headers)

    proxy_header_account_id = _extract_header_value(POSSIBLE_PROXY_HEADER_ACCOUNT_ID, headers)
    proxy_header_api_id = _extract_header_value(POSSIBLE_PROXY_HEADER_API_ID, headers)
    proxy_header_region = _extract_header_value(POSSIBLE_PROXY_HEADER_REGION, headers)
    proxy_header_user = _extract_header_value(POSSIBLE_PROXY_HEADER_USER, headers)

    header_user_agent = _extract_header_value(HEADER_USERAGENT, headers)

    # Exit if start time header is not present
    if proxy_header_start_time_ms is None:
        return None

    # Exit if proxy header system name is not present or is a system we don't support
    if not (proxy_header_system and proxy_header_system in supported_proxies):
        log.debug(
            "Received headers to create inferred proxy span but headers include an unsupported proxy type", headers
        )
        return None

    return ProxyHeaderContext(
        proxy_header_system,
        proxy_header_start_time_ms,
        proxy_header_httpmethod,
        proxy_header_path,
        proxy_header_resource_path,
        proxy_header_domain,
        proxy_header_stage,
        proxy_header_account_id,
        proxy_header_api_id,
        proxy_header_region,
        proxy_header_user,
        header_user_agent,
    )


def normalize_headers(headers) -> Dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}
