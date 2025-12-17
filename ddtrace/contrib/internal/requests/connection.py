from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import Optional  # noqa:F401
from urllib import parse

import requests

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import _sanitized_url
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import USER_AGENT_HEADER
from ddtrace.internal.logger import get_logger
from ddtrace.internal.opentelemetry.constants import OTLP_EXPORTER_HEADER_IDENTIFIER
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
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


def _extract_hostname_and_path(uri):
    # type: (str) -> str
    parsed_uri = parse.urlparse(uri)
    hostname = parsed_uri.hostname
    try:
        if parsed_uri.port is not None:
            hostname = "%s:%s" % (hostname, str(parsed_uri.port))
    except ValueError:
        # ValueError is raised in PY>3.5 when parsed_uri.port < 0 or parsed_uri.port > 65535
        hostname = "%s:?" % (hostname,)
    return hostname, parsed_uri.path


def _extract_query_string(uri):
    # type: (str) -> Optional[str]
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


def _wrap_send(func, instance, args, kwargs):
    """Trace the `Session.send` instance method"""
    request = get_argument_value(args, kwargs, 0, "request")
    if not request or is_otlp_export(request):
        return func(*args, **kwargs)

    # Check if tracing is enabled (but allow if APM is opted out for ASM)
    import ddtrace
    from ddtrace.internal.settings.asm import config as asm_config

    if not ddtrace.tracer.enabled and not asm_config._apm_opt_out:
        return func(*args, **kwargs)

    url = _sanitized_url(request.url)
    method = ""
    if request.method is not None:
        method = request.method.upper()
    hostname, path = _extract_hostname_and_path(url)
    host_without_port = hostname.split(":")[0] if hostname is not None else None

    parsed_uri = parse.urlparse(url)

    # Get pin from instance to access integration config
    pin = Pin.get_from(instance)

    # Use pin's config if available, otherwise fall back to global config
    integration_config = pin._config if pin else config.requests

    # Determine service name (check pin config for "service" or "service_name")
    service = None
    if integration_config and isinstance(integration_config, dict):
        service = integration_config.get("service") or integration_config.get("service_name")
    if service is None:
        service = trace_utils.ext_service(pin, config.requests)

    # AppSec Integration: Provide context items for RASP and API Security (API10)
    # The requests library calls urllib3 methods that have AppSec wrappers (see _common_module_patches.py).
    # These wrappers expect to find 'full_url' and 'use_body' via core.get_item() to:
    #   - full_url: Analyze outbound requests for SSRF threats (e.g., requests to 169.254.169.254)
    #   - use_body: Determine if response bodies should be analyzed for sensitive data leakage
    # Without these context items, AppSec security checks are skipped for requests made via this library.
    full_url = url
    use_body = False
    try:
        from ddtrace.appsec._asm_request_context import _get_asm_context
        from ddtrace.appsec._asm_request_context import should_analyze_body_response

        if asm_context := _get_asm_context():
            use_body = should_analyze_body_response(asm_context)
    except ImportError:
        # AppSec is not available, which is fine - not all users have it enabled
        pass

        # the above should be handled by a asm event - dispatch the event and use the result somehow

    with core.context_with_data(
        "requests.send",
        span_name=schematize_url_operation("requests.request", protocol="http", direction=SpanDirection.OUTBOUND),
        pin=pin,
        service=service,
        resource=f"{method} {path}",
        span_type=SpanTypes.HTTP,
        integration_config=integration_config,
        measured=True,
        tags={COMPONENT: config.requests.integration_name, SPAN_KIND: SpanKind.CLIENT},
        request=request,
        request_url=url,
        request_method=method,
        request_headers=request.headers,
        hostname=hostname,
        path=path,
        host_without_port=host_without_port,
        parsed_uri=parsed_uri,
        full_url=full_url,
        use_body=use_body,
    ) as ctx:
        response = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            ctx.set_item("response", response)
