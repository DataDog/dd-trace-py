from urllib import parse

import urllib3
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.settings.asm import config as asm_config
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
    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_request
        from ddtrace.appsec._common_module_patches import wrapped_urllib3_make_request_6D4E8B2A1F095C73 as _make_request

        _w("urllib3.connectionpool", "HTTPConnectionPool._make_request", _make_request)
        if hasattr(urllib3, "_request_methods"):
            _w("urllib3._request_methods", "RequestMethods.request", _wrap_request)
        else:
            # Old version before https://github.com/urllib3/urllib3/pull/2398
            _w("urllib3.request", "RequestMethods.request", _wrap_request)


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
        request_url = parse.urlunparse(
            (
                instance.scheme,
                "{}:{}".format(instance.host, instance.port)
                if instance.port and instance.port not in DROP_PORTS
                else str(instance.host),
                request_url,
                None,
                None,
                None,
            )
        )

    parsed_uri = parse.urlparse(request_url)
    hostname = parsed_uri.netloc

    if not is_tracing_enabled():
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
