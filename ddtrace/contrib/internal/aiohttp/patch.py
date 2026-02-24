import os

import aiohttp
import wrapt
from yarl import URL

from ddtrace import config
from ddtrace.contrib._events.aiohttp import AIOHttpConnectEvent
from ddtrace.contrib._events.aiohttp import AIOHttpCreateConnectEvent
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils import extract_netloc_and_query_info_from_url
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool


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


def get_version() -> str:
    return aiohttp.__version__


def _supported_versions() -> dict[str, str]:
    return {"aiohttp": ">=3.7"}


class _WrappedConnectorClass(wrapt.ObjectProxy):
    async def connect(self, req, *args, **kwargs):
        with core.context_with_event(
            AIOHttpConnectEvent(
                component=config.aiohttp.integration_name,
                connector_class_name=self.__class__.__name__,
            )
        ):
            result = await self.__wrapped__.connect(req, *args, **kwargs)
            return result

    async def _create_connection(self, req, *args, **kwargs):
        with core.context_with_event(
            AIOHttpCreateConnectEvent(
                component=config.aiohttp.integration_name,
                connector_class_name=self.__class__.__name__,
            )
        ):
            result = await self.__wrapped__._create_connection(req, *args, **kwargs)
            return result


async def _traced_clientsession_request(func, instance, args, kwargs):
    method: str = get_argument_value(args, kwargs, 0, "method")
    url: URL = URL(get_argument_value(args, kwargs, 1, "url"))
    params = kwargs.get("params")
    headers = kwargs.get("headers") or {}
    if kwargs.get("headers") is None:
        kwargs["headers"] = headers

    # Params can be included separate of the URL so the URL has to be constructed
    # with the passed params.
    url_str = str(url.update_query(params) if params else url)
    host, query = extract_netloc_and_query_info_from_url(url_str)
    service = url.host if config.aiohttp_client.split_by_domain else None

    with core.context_with_event(
        HttpClientRequestEvent(
            component=config.aiohttp_client.integration_name,
            http_operation="aiohttp.request",
            service=service,
            request_method=method,
            request_headers=headers,
            config=config.aiohttp_client,
            url=str(url),
            query=query,
            target_host=host,
        )
    ) as ctx:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                event: HttpClientRequestEvent = ctx.event
                event.response_headers = resp.headers
                event.response_status_code = resp.status
                event.response_status_msg = resp.reason


def _traced_clientsession_init(func, instance, args, kwargs):
    func(*args, **kwargs)
    instance._connector = _WrappedConnectorClass(instance._connector)


def _patch_client(aiohttp):
    wrap("aiohttp", "ClientSession.__init__", _traced_clientsession_init)
    wrap("aiohttp", "ClientSession._request", _traced_clientsession_request)


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
