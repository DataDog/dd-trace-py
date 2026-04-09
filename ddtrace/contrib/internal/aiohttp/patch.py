from typing import Optional

import aiohttp
import wrapt
from yarl import URL

from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import extract_netloc_and_query_info_from_url
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings import env
from ddtrace.internal.settings._config import config
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


log = get_logger(__name__)

# Server config
config._add(
    "aiohttp",
    dict(
        distributed_tracing=True,
        disable_stream_timing_for_mem_leak=asbool(
            _get_config("DD_AIOHTTP_CLIENT_DISABLE_STREAM_TIMING_FOR_MEM_LEAK", default=False)
        ),
    ),
)

config._add(
    "aiohttp_client",
    dict(
        distributed_tracing=asbool(env.get("DD_AIOHTTP_CLIENT_DISTRIBUTED_TRACING", True)),
        default_http_tag_query_string=config._http_client_tag_query_string,
        split_by_domain=asbool(env.get("DD_AIOHTTP_CLIENT_SPLIT_BY_DOMAIN", default=False)),
    ),
)


def get_version() -> str:
    return aiohttp.__version__


def _supported_versions() -> dict[str, str]:
    return {"aiohttp": ">=3.7"}


class _WrappedConnectorClass(wrapt.ObjectProxy):
    def __init__(self, obj):
        super().__init__(obj)

    async def connect(self, req, *args, **kwargs):
        with tracer.trace("%s.connect" % self.__class__.__name__) as span:
            # set component tag equal to name of integration
            span.set_tag(COMPONENT, config.aiohttp.integration_name)
            result = await self.__wrapped__.connect(req, *args, **kwargs)
            return result

    async def _create_connection(self, req, *args, **kwargs):
        with tracer.trace("%s._create_connection" % self.__class__.__name__) as span:
            # set component tag equal to name of integration
            span.set_tag(COMPONENT, config.aiohttp.integration_name)
            result = await self.__wrapped__._create_connection(req, *args, **kwargs)
            return result


async def _traced_clientsession_request(func, instance, args, kwargs):
    method: str = get_argument_value(args, kwargs, 0, "method")
    raw_url: URL = URL(str(get_argument_value(args, kwargs, 1, "url")))
    # Resolve against base_url if present, mirroring aiohttp's internal behaviour.
    base_url: Optional[URL] = getattr(instance, "_base_url", None)
    url: URL = base_url.join(raw_url) if base_url is not None else raw_url
    params = kwargs.get("params")
    headers = kwargs.get("headers") or {}
    service: str = url.host if config.aiohttp_client.split_by_domain else ext_service(None, config.aiohttp_client)

    with tracer.trace(
        schematize_url_operation("aiohttp.request", protocol="http", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.HTTP,
    ) as span:
        set_service_and_source(span, service, config.aiohttp_client)

        if config.aiohttp_client.distributed_tracing:
            HTTPPropagator.inject(span.context, headers)
            kwargs["headers"] = headers

        span._set_attribute(COMPONENT, config.aiohttp_client.integration_name)

        # set span.kind tag equal to type of request
        span._set_attribute(SPAN_KIND, SpanKind.CLIENT)

        # Params can be included separate of the URL so the URL has to be constructed
        # with the passed params.
        url_str = str(url.update_query(params) if params else url)
        host, query = extract_netloc_and_query_info_from_url(url_str)
        set_http_meta(
            span,
            config.aiohttp_client,
            method=method,
            url=str(url),
            target_host=host,
            query=query,
            request_headers=headers,
        )
        resp: aiohttp.ClientResponse = await func(*args, **kwargs)
        set_http_meta(
            span, config.aiohttp_client, response_headers=resp.headers, status_code=resp.status, status_msg=resp.reason
        )
        return resp


def _traced_clientsession_init(func, instance, args, kwargs):
    func(*args, **kwargs)
    instance._connector = _WrappedConnectorClass(instance._connector)


def patch():
    if getattr(aiohttp, "_datadog_patch", False):
        return

    wrap("aiohttp", "ClientSession.__init__", _traced_clientsession_init)
    wrap("aiohttp", "ClientSession._request", _traced_clientsession_request)

    aiohttp._datadog_patch = True


def _unpatch_client(aiohttp):
    unwrap(aiohttp.ClientSession, "__init__")
    unwrap(aiohttp.ClientSession, "_request")


def unpatch():
    import aiohttp

    if not getattr(aiohttp, "_datadog_patch", False):
        return

    _unpatch_client(aiohttp)

    aiohttp._datadog_patch = False
