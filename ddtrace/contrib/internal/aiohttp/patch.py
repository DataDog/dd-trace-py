import aiohttp
import wrapt
from yarl import URL

from ddtrace import config
from ddtrace.contrib._events.generic import GenericOperationEvent
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils import extract_netloc_and_query_info_from_url
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
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
    async def connect(self, req, *args, **kwargs):
        with core.context_with_event(
            GenericOperationEvent(
                span_name="%s.connect" % self.__class__.__name__,
                component=config.aiohttp.integration_name,
                config=config.aiohttp,
            ),
        ):
            return await self.__wrapped__.connect(req, *args, **kwargs)

    async def _create_connection(self, req, *args, **kwargs):
        with core.context_with_event(
            GenericOperationEvent(
                span_name="%s._create_connection" % self.__class__.__name__,
                component=config.aiohttp.integration_name,
                config=config.aiohttp,
            ),
        ):
            return await self.__wrapped__._create_connection(req, *args, **kwargs)


async def _traced_clientsession_request(func, instance, args, kwargs):
    method = get_argument_value(args, kwargs, 0, "method")
    raw_url = URL(str(get_argument_value(args, kwargs, 1, "url")))
    base_url = getattr(instance, "_base_url", None)
    url = base_url.join(raw_url) if base_url is not None else raw_url
    params = kwargs.get("params")
    headers = kwargs.get("headers") or {}
    kwargs["headers"] = headers

    url_str = str(url.update_query(params) if params else url)
    host, query = extract_netloc_and_query_info_from_url(url_str)

    with core.context_with_event(
        HttpClientRequestEvent(
            http_operation="aiohttp.request",
            component=config.aiohttp_client.integration_name,
            config=config.aiohttp_client,
            request_method=method,
            request_headers=headers,
            url=str(url),
            query=query,
            target_host=host,
            split_by_domain_target=str(url.host),
            measured=False,
        ),
    ) as ctx:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.response_status_code = resp.status
                ctx.event.response_headers = resp.headers
                ctx.event.response_status_msg = resp.reason


def _traced_clientsession_init(func, instance, args, kwargs):
    func(*args, **kwargs)
    instance._connector = _WrappedConnectorClass(instance._connector)


def _patch_client(aiohttp):
    wrap("aiohttp", "ClientSession.__init__", _traced_clientsession_init)
    wrap("aiohttp", "ClientSession._request", _traced_clientsession_request)


def patch():
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
