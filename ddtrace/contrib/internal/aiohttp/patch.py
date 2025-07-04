import os
from typing import Dict

import aiohttp
import wrapt
from yarl import URL

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import extract_netloc_and_query_info_from_url
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module as with_traced_module_sync
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.internal.trace_utils_async import with_traced_module
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Pin


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
        distributed_tracing=asbool(os.getenv("DD_AIOHTTP_CLIENT_DISTRIBUTED_TRACING", True)),
        default_http_tag_query_string=config._http_client_tag_query_string,
        split_by_domain=asbool(os.getenv("DD_AIOHTTP_CLIENT_SPLIT_BY_DOMAIN", default=False)),
    ),
)


def get_version():
    # type: () -> str
    return aiohttp.__version__


def _supported_versions() -> Dict[str, str]:
    return {"aiohttp": ">=3.7"}


class _WrappedConnectorClass(wrapt.ObjectProxy):
    def __init__(self, obj, pin):
        super().__init__(obj)
        pin.onto(self)

    async def connect(self, req, *args, **kwargs):
        pin = Pin.get_from(self)
        with pin.tracer.trace("%s.connect" % self.__class__.__name__) as span:
            # set component tag equal to name of integration
            span.set_tag(COMPONENT, config.aiohttp.integration_name)
            result = await self.__wrapped__.connect(req, *args, **kwargs)
            return result

    async def _create_connection(self, req, *args, **kwargs):
        pin = Pin.get_from(self)
        with pin.tracer.trace("%s._create_connection" % self.__class__.__name__) as span:
            # set component tag equal to name of integration
            span.set_tag(COMPONENT, config.aiohttp.integration_name)
            result = await self.__wrapped__._create_connection(req, *args, **kwargs)
            return result


@with_traced_module
async def _traced_clientsession_request(aiohttp, pin, func, instance, args, kwargs):
    method = get_argument_value(args, kwargs, 0, "method")  # type: str
    url = URL(get_argument_value(args, kwargs, 1, "url"))  # type: URL
    params = kwargs.get("params")
    headers = kwargs.get("headers") or {}

    with pin.tracer.trace(
        schematize_url_operation("aiohttp.request", protocol="http", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.HTTP,
        service=ext_service(pin, config.aiohttp_client),
    ) as span:
        if config.aiohttp_client.split_by_domain:
            span.service = url.host

        if pin._config["distributed_tracing"]:
            HTTPPropagator.inject(span.context, headers)
            kwargs["headers"] = headers

        span.set_tag_str(COMPONENT, config.aiohttp_client.integration_name)

        # set span.kind tag equal to type of request
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

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
        resp = await func(*args, **kwargs)  # type: aiohttp.ClientResponse
        set_http_meta(
            span, config.aiohttp_client, response_headers=resp.headers, status_code=resp.status, status_msg=resp.reason
        )
        return resp


@with_traced_module_sync
def _traced_clientsession_init(aiohttp, pin, func, instance, args, kwargs):
    func(*args, **kwargs)
    instance._connector = _WrappedConnectorClass(instance._connector, pin)


def _patch_client(aiohttp):
    Pin().onto(aiohttp)
    pin = Pin(_config=config.aiohttp_client.copy())
    pin.onto(aiohttp.ClientSession)

    wrap("aiohttp", "ClientSession.__init__", _traced_clientsession_init(aiohttp))
    wrap("aiohttp", "ClientSession._request", _traced_clientsession_request(aiohttp))


def patch():
    import aiohttp

    if getattr(aiohttp, "_datadog_patch", False):
        return

    _patch_client(aiohttp)

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
