import sys
from types import ModuleType
from typing import Optional
from urllib import parse

from wrapt import wrap_function_wrapper as _w

from ddtrace._trace.settings import DistributedTracingConfigMixin
from ddtrace._trace.settings import HttpIntegrationConfigMixin
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings._config import config as _global_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings.integration import IntegrationEnvConfig
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.wrappers import unwrap as _u


# Ports which, if set, will not be used in hostnames/service names
DROP_PORTS = (80, 443)

# IntegrationPlugin surface.
name = "urllib3"
default_enabled = False
supported_versions = {"urllib3": ">=1.25.0"}


class Urllib3Config(IntegrationEnvConfig, HttpIntegrationConfigMixin, DistributedTracingConfigMixin):
    split_by_domain = DDConfig.v(bool, "split_by_domain", default=False)

    _default_service = schematize_service_name("urllib3")
    default_http_tag_query_string = _global_config._http_client_tag_query_string


Urllib3Retry: Optional[type] = None


def _set_retry_class(retry_module: ModuleType) -> None:
    global Urllib3Retry
    Urllib3Retry = retry_module.Retry


def _patch_connectionpool(connectionpool: ModuleType) -> None:
    _w(connectionpool.HTTPConnectionPool, "urlopen", _wrap_urlopen)


def _unpatch_connectionpool(connectionpool: ModuleType) -> None:
    _u(connectionpool.HTTPConnectionPool, "urlopen")


def enable() -> None:
    ModuleWatchdog.register_module_hook("urllib3.util.retry", _set_retry_class)
    ModuleWatchdog.register_module_hook("urllib3.connectionpool", _patch_connectionpool)


def disable() -> None:
    ModuleWatchdog.unregister_module_hook("urllib3.connectionpool", _patch_connectionpool)
    ModuleWatchdog.unregister_module_hook("urllib3.util.retry", _set_retry_class)

    connectionpool = sys.modules.get("urllib3.connectionpool")
    if connectionpool is not None:
        _unpatch_connectionpool(connectionpool)


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
                f"{instance.host}:{instance.port}"
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

    int_config = _global_config.urllib3
    service = hostname if int_config.split_by_domain else trace_utils.ext_service(None, int_config)

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
            component=int_config.integration_name,
            integration_config=int_config,
            request_method=str(request_method),
            request_headers=request_headers,
            request_url=ensure_text(request_url),
            query=ensure_text(parsed_uri.query),
            target_host=instance.host,
            server_address=instance.host,
            retries_remain=(
                request_retries.total
                if Urllib3Retry is not None and isinstance(request_retries, Urllib3Retry)
                else None
            ),
        )
    ) as ctx:
        response = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            if response is not None:
                ctx.event.set_response(response)
