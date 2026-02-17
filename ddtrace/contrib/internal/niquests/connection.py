from typing import Any
from typing import Dict
from urllib import parse

import niquests

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import _sanitized_url
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import USER_AGENT_HEADER
from ddtrace.internal.logger import get_logger
from ddtrace.internal.opentelemetry.constants import OTLP_EXPORTER_HEADER_IDENTIFIER
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


log = get_logger(__name__)


def is_otlp_export(request: niquests.models.Request) -> bool:
    if not (config._otel_logs_enabled or config._otel_metrics_enabled):
        return False
    user_agent = request.headers.get(USER_AGENT_HEADER, "")
    normalized_user_agent = user_agent.lower().replace(" ", "-")
    if OTLP_EXPORTER_HEADER_IDENTIFIER in normalized_user_agent:
        return True
    return False


def _extract_hostname_and_path(uri):
    parsed_uri = parse.urlparse(uri)
    hostname = parsed_uri.hostname
    try:
        if parsed_uri.port is not None:
            hostname = "%s:%s" % (hostname, str(parsed_uri.port))
    except ValueError:
        hostname = "%s:?" % (hostname,)
    return hostname, parsed_uri.path


def _extract_query_string(uri):
    start = uri.find("?") + 1
    if start == 0:
        return None

    end = len(uri)
    j = uri.rfind("#", 0, end)
    if j != -1:
        end = j

    if end <= start:
        return None

    return uri[start:end]


def _wrap_send_sync(func, instance, args, kwargs):
    if not tracer.enabled and not asm_config._apm_opt_out:
        return func(*args, **kwargs)
    request = get_argument_value(args, kwargs, 0, "request")
    if not request or is_otlp_export(request):
        return func(*args, **kwargs)

    url = _sanitized_url(request.url)
    method = ""
    if request.method is not None:
        method = request.method.upper()
    hostname, path = _extract_hostname_and_path(url)
    if hostname is not None:
        host_without_port = hostname.split(":")[0]
    else:
        host_without_port = None

    cfg: Dict[str, Any] = {}
    pin = Pin.get_from(instance)
    if pin:
        cfg = pin._config

    service = None
    if cfg.get("split_by_domain") and hostname:
        service = hostname
    if service is None:
        service = cfg.get("service", None)
    if service is None:
        service = cfg.get("service_name", None)
    if service is None:
        service = trace_utils.ext_service(None, config.niquests)

    operation_name = schematize_url_operation("niquests.request", protocol="http", direction=SpanDirection.OUTBOUND)
    with tracer.trace(operation_name, service=service, resource=f"{method} {path}", span_type=SpanTypes.HTTP) as span:
        span._set_tag_str(COMPONENT, config.niquests.integration_name)
        span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span.set_metric(_SPAN_MEASURED_KEY, 1)
        if cfg.get("distributed_tracing"):
            HTTPPropagator.inject(span.context, request.headers)

        response = response_headers = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            try:
                status = None
                if response is not None:
                    status = response.status_code
                    response_headers = dict(getattr(response, "headers", {}))

                trace_utils.set_http_meta(
                    span,
                    config.niquests,
                    request_headers=request.headers,
                    response_headers=response_headers,
                    method=method,
                    url=request.url,
                    target_host=host_without_port,
                    status_code=status,
                    query=_extract_query_string(url),
                )
            except Exception:
                log.debug("niquests: error adding tags", exc_info=True)


async def _wrap_send_async(func, instance, args, kwargs):
    if not tracer.enabled and not asm_config._apm_opt_out:
        return await func(*args, **kwargs)
    request = get_argument_value(args, kwargs, 0, "request")
    if not request or is_otlp_export(request):
        return await func(*args, **kwargs)

    url = _sanitized_url(request.url)
    method = ""
    if request.method is not None:
        method = request.method.upper()
    hostname, path = _extract_hostname_and_path(url)
    if hostname is not None:
        host_without_port = hostname.split(":")[0]
    else:
        host_without_port = None

    cfg: Dict[str, Any] = {}
    pin = Pin.get_from(instance)
    if pin:
        cfg = pin._config

    service = None
    if cfg.get("split_by_domain") and hostname:
        service = hostname
    if service is None:
        service = cfg.get("service", None)
    if service is None:
        service = cfg.get("service_name", None)
    if service is None:
        service = trace_utils.ext_service(None, config.niquests)

    operation_name = schematize_url_operation("niquests.request", protocol="http", direction=SpanDirection.OUTBOUND)
    with tracer.trace(operation_name, service=service, resource=f"{method} {path}", span_type=SpanTypes.HTTP) as span:
        span._set_tag_str(COMPONENT, config.niquests.integration_name)
        span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span.set_metric(_SPAN_MEASURED_KEY, 1)
        if cfg.get("distributed_tracing"):
            HTTPPropagator.inject(span.context, request.headers)

        response = response_headers = None
        try:
            response = await func(*args, **kwargs)
            return response
        finally:
            try:
                status = None
                if response is not None:
                    status = response.status_code
                    response_headers = dict(getattr(response, "headers", {}))

                trace_utils.set_http_meta(
                    span,
                    config.niquests,
                    request_headers=request.headers,
                    response_headers=response_headers,
                    method=method,
                    url=request.url,
                    target_host=host_without_port,
                    status_code=status,
                    query=_extract_query_string(url),
                )
            except Exception:
                log.debug("niquests: error adding tags", exc_info=True)
