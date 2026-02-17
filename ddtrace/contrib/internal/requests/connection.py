from typing import Optional  # noqa:F401
from urllib import parse

import requests

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils import _sanitized_url
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.internal import core
from ddtrace.internal.constants import USER_AGENT_HEADER
from ddtrace.internal.logger import get_logger
from ddtrace.internal.opentelemetry.constants import OTLP_EXPORTER_HEADER_IDENTIFIER
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils import get_argument_value


log = get_logger(__name__)


def is_otlp_export(request: requests.models.Request) -> bool:
    """Determine if a request is submitting data to the OpenTelemetry OTLP exporter."""
    if not (config._otel_logs_enabled or config._otel_metrics_enabled):
        return False
    user_agent = request.headers.get(USER_AGENT_HEADER, "")
    normalized_user_agent = user_agent.lower().replace(" ", "-")
    if OTLP_EXPORTER_HEADER_IDENTIFIER in normalized_user_agent:
        return True
    return False


def _extract_hostname_and_path(uri: str) -> str:
    parsed_uri = parse.urlparse(uri)
    hostname = parsed_uri.hostname
    try:
        if parsed_uri.port is not None:
            hostname = "%s:%s" % (hostname, str(parsed_uri.port))
    except ValueError:
        # ValueError is raised in PY>3.5 when parsed_uri.port < 0 or parsed_uri.port > 65535
        hostname = "%s:?" % (hostname,)
    return hostname, parsed_uri.path


def _extract_query_string(uri: str) -> Optional[str]:
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


def _get_service_name(request, hostname) -> Optional[str]:
    if config.requests["split_by_domain"] and hostname:
        return hostname

    return ext_service(None, config.requests)


def _wrap_send(func, instance, args, kwargs):
    """Trace the `Session.send` instance method"""
    # skip if tracing is not enabled
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
    host_without_port = hostname.split(":")[0] if hostname is not None else None

    with core.context_with_event(
        HttpClientRequestEvent(
            operation_name="requests.request",
            service=_get_service_name(request, hostname),
            resource=f"{method} {path}",
            config=config.requests,
            request=request,
            url=request.url,
            query=_extract_query_string(url),
            target_host=host_without_port,
        ),
    ) as ctx:
        response = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            ctx.set_item("response", response)
