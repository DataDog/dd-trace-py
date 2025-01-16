import logging
from typing import Dict

from ddtrace import config
from ddtrace import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import http
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT

log = logging.getLogger(__name__)

PROXY_HEADER_SYSTEM = 'x-dd-proxy'
PROXY_HEADER_START_TIME_MS = 'x-dd-proxy-request-time-ms'
PROXY_HEADER_PATH = 'x-dd-proxy-path'
PROXY_HEADER_HTTPMETHOD = 'x-dd-proxy-httpmethod'
PROXY_HEADER_DOMAIN = 'x-dd-proxy-domain-name'
PROXY_HEADER_STAGE = 'x-dd-proxy-stage'

supported_proxies: Dict[str, Dict[str, str]] = {
    'aws-apigateway': {
        'span_name': 'aws.apigateway',
        'component': 'aws-apigateway'
    }
}

def create_inferred_proxy_span_if_headers_exist(headers, child_of, tracer): #-> Optional[Tuple[Optional[Span], Optional[function]]]:
    if not headers:
        return None

    if not config._inferred_proxy_services_enabled:
        return None

    headers = normalize_headers(headers)

    proxy_context = extract_inferred_proxy_context(headers)

    if not proxy_context:
        return None

    proxy_span_info = supported_proxies[proxy_context['proxy_system_name']]

    log.debug(f'Successfully extracted inferred span info {proxy_context} for proxy: {proxy_context["proxy_system_name"]}')

    proxy_method = proxy_context['method']
    proxy_path = proxy_context['path']
    proxy_resource_name = proxy_method + " " + proxy_path

    span = tracer.start_span(
        proxy_span_info['span_name'],
        service=proxy_context.get('domain_name', config._get_service()),
        resource=proxy_resource_name,
        span_type=SpanTypes.WEB,
        activate=True,
        child_of=child_of,
    )
    span.start_ns = proxy_context['request_time'] * 1000000

    log.debug('Successfully created inferred proxy span.')

    set_inferred_proxy_span_tags(span, proxy_context)

    # we need a callback to finish the api gateway span, this callback will be added to the child spans finish callbacks
    def finish_callback(_):
        web_span = _
        # Fill in other details from the web span once we know it
        if web_span and span:
            span.set_tag('http.status_code', web_span.get_tag('http.status_code'))
            span.error = web_span.error
            if web_span.get_tag(ERROR_MSG):
                span.set_tag(ERROR_MSG, web_span.get_tag(ERROR_MSG))
            if web_span.get_tag(ERROR_TYPE):
                span.set_tag(ERROR_TYPE, web_span.get_tag(ERROR_TYPE))
            if web_span.get_tag(ERROR_STACK):
                span.set_tag(ERROR_STACK, web_span.get_tag(ERROR_STACK))
        span.finish()

    headers = delete_inferred_header_keys(headers)

    return span, finish_callback, headers

def set_inferred_proxy_span_tags(span, proxy_context):
    span.set_tag_str(COMPONENT, supported_proxies[proxy_context['proxy_system_name']]['component'])
    span.set_tag_str(SPAN_KIND, SpanKind.INTERNAL)

    span.set_tag_str(http.METHOD, proxy_context['method'])
    span.set_tag_str(http.URL, f"{proxy_context['domain_name']}{proxy_context['path']}")
    span.set_tag_str(http.ROUTE, proxy_context['path'])
    span.set_tag_str('stage', proxy_context['stage'])

    span.set_tag_str('_dd.inferred_span', '1')
    return span

def extract_inferred_proxy_context(headers):
    if PROXY_HEADER_START_TIME_MS not in headers:
        return None

    if not (PROXY_HEADER_SYSTEM in headers and headers[PROXY_HEADER_SYSTEM] in supported_proxies):
        log.debug(f'Received headers to create inferred proxy span but headers include an unsupported proxy type {headers}')
        return None

    return {
        'request_time': int(headers[PROXY_HEADER_START_TIME_MS]) if headers[PROXY_HEADER_START_TIME_MS] else None,
        'method': headers[PROXY_HEADER_HTTPMETHOD],
        'path': headers[PROXY_HEADER_PATH],
        'stage': headers[PROXY_HEADER_STAGE],
        'domain_name': headers[PROXY_HEADER_DOMAIN],
        'proxy_system_name': headers[PROXY_HEADER_SYSTEM]
    }


def normalize_headers(headers):
    return {key.lower(): value for key, value in headers.items()}


def delete_inferred_header_keys(headers):
    keys_to_delete = [
        PROXY_HEADER_START_TIME_MS,
        PROXY_HEADER_HTTPMETHOD,
        PROXY_HEADER_PATH,
        PROXY_HEADER_STAGE,
        PROXY_HEADER_DOMAIN,
        PROXY_HEADER_SYSTEM
    ]

    for key in keys_to_delete:
        try:
            del headers[key]
        except KeyError:
            pass

    return headers
