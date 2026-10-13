from typing import Mapping
from typing import Optional
from typing import Union
from urllib import parse

import urllib3
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.constants import OTLP_EXPORTER_HEADER_IDENTIFIER
from ddtrace.internal.constants import USER_AGENT_HEADER
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.settings._opentelemetry import ExporterConfig
from ddtrace.internal.settings._opentelemetry import _is_otlp_traces_exporter_enabled
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


# Ports which, if set, will not be used in hostnames/service names
DROP_PORTS = (80, 443)

# Initialize the default config vars
config._add(
    "urllib3",
    {
        "_default_service": schematize_service_name("urllib3"),
        "distributed_tracing": asbool(env.get("DD_URLLIB3_DISTRIBUTED_TRACING", default=True)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        "split_by_domain": asbool(env.get("DD_URLLIB3_SPLIT_BY_DOMAIN", default=False)),
    },
)


def get_version() -> str:
    return getattr(urllib3, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"urllib3": ">=1.25.0"}


def patch():
    """Enable tracing for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        return
    urllib3.__datadog_patch = True

    _w("urllib3", "connectionpool.HTTPConnectionPool.urlopen", _wrap_urlopen)


def unpatch():
    """Disable trace for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        urllib3.__datadog_patch = False

        _u(urllib3.connectionpool.HTTPConnectionPool, "urlopen")


def _wrap_urlopen(func, instance, args, kwargs):
    """
    Wrapper function for the lower-level urlopen in urllib3

    :param func: The original target function "urlopen"
    :param instance: The patched instance of ``HTTPConnectionPool``
    :param args: Positional arguments from the target function
    :param kwargs: Keyword arguments from the target function
    :return: The ``HTTPResponse`` from the target function
    """
    request_method = get_argument_value(args, kwargs, 0, "method")
    request_url = get_argument_value(args, kwargs, 1, "url")
    try:
        request_headers = get_argument_value(args, kwargs, 3, "headers")
    except ArgumentError:
        request_headers = None
    try:
        request_retries = get_argument_value(args, kwargs, 4, "retries")
    except ArgumentError:
        request_retries = None

    # HTTPConnectionPool allows relative path requests; convert the request_url to an absolute url
    if request_url.startswith("/"):
        host = str(instance.host)
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        request_url = parse.urlunparse(
            (
                instance.scheme,
                f"{host}:{instance.port}" if instance.port and instance.port not in DROP_PORTS else host,
                request_url,
                None,
                None,
                None,
            )
        )

    parsed_uri = parse.urlparse(request_url)
    hostname = parsed_uri.netloc

    if not is_tracing_enabled() or _is_otlp_export(request_method, request_url, request_headers):
        return func(*args, **kwargs)

    service = hostname if config.urllib3.split_by_domain else trace_utils.ext_service(None, config.urllib3)

    # Ensure headers is always a mutable mapping for HttpClientRequestEvent subscribers.
    # Distributed tracing enablement is handled by subscribers (via integration config).
    if request_headers is None:
        request_headers = {}
        kwargs["headers"] = request_headers

    with core.context_with_event(
        HttpClientRequestEvent(
            http_operation="urllib3.request",
            service=service,
            measured=False,
            component=config.urllib3.integration_name,
            integration_config=config.urllib3,
            request_method=str(request_method),
            request_headers=request_headers,
            request_url=ensure_text(request_url),
            query=ensure_text(parsed_uri.query),
            target_host=instance.host,
            server_address=instance.host,
            retries_remain=request_retries.total if isinstance(request_retries, urllib3.util.retry.Retry) else None,
        )
    ) as ctx:
        response = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            if response is not None:
                ctx.event.set_response(response)


def _is_otlp_export(method: str, url: str, headers: Optional[Mapping[str, Union[str, bytes]]]) -> bool:
    if not config._otel_enabled:
        return False
    if headers:
        for name, value in headers.items():
            if name.lower() == USER_AGENT_HEADER:
                normalized_user_agent = ensure_text(value).lower().replace(" ", "-")
                if OTLP_EXPORTER_HEADER_IDENTIFIER in normalized_user_agent:
                    return True
                break
    return method.upper() == "POST" and _normalize_otlp_url(url) in _OTLP_EXPORT_URLS


def _normalize_otlp_url(url: str) -> Optional[tuple]:
    parsed = parse.urlparse(url)
    if not parsed.hostname:
        return None
    port = parsed.port
    if port == {"http": 80, "https": 443}.get(parsed.scheme.lower()):
        port = None
    return (parsed.scheme, parsed.hostname, port, parsed.path.rstrip("/"))


def _otlp_export_urls() -> frozenset:
    """Full export URLs for the enabled OTLP signals, resolved as the OpenTelemetry SDK does."""
    base = env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    urls = set()
    for enabled, protocol, signal_endpoint_var, endpoint, path in (
        (
            config._otel_metrics_enabled,
            otel_config.exporter.METRICS_PROTOCOL,
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
            otel_config.exporter.METRICS_ENDPOINT,
            ExporterConfig.METRICS_PATH,
        ),
        (
            config._otel_logs_enabled,
            otel_config.exporter.LOGS_PROTOCOL,
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
            otel_config.exporter.LOGS_ENDPOINT,
            ExporterConfig.LOGS_PATH,
        ),
        (
            _is_otlp_traces_exporter_enabled(otel_config.exporter),
            otel_config.exporter.TRACES_PROTOCOL,
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            otel_config.exporter.TRACES_ENDPOINT,
            ExporterConfig.TRACES_PATH,
        ),
    ):
        if not enabled or protocol is None or protocol.lower() != "http/protobuf":
            continue
        url = base.rstrip("/") + path if base and not env.get(signal_endpoint_var) else endpoint
        normalized = _normalize_otlp_url(url)
        if normalized is not None:
            urls.add(normalized)
    return frozenset(urls)


_OTLP_EXPORT_URLS = _otlp_export_urls()
