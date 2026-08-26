import httpx

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.ext import SpanKind
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version

from .common import HttpxPatcher


HTTPX_VERSION = parse_version(httpx.__version__)
HTTP_REQUEST_TAGS = {COMPONENT: config.httpx.integration_name, SPAN_KIND: SpanKind.CLIENT}


def get_version() -> str:
    return getattr(httpx, "__version__", "")


config._add(
    "httpx",
    {
        "distributed_tracing": asbool(env.get("DD_HTTPX_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(env.get("DD_HTTPX_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
    },
)


def _supported_versions() -> dict[str, str]:
    return {"httpx": ">=0.25"}


_patcher = HttpxPatcher(
    httpx,
    config.httpx,
    request_event_name=HttpClientEvents.HTTPX_REQUEST.value,
    send_event_name=HttpClientEvents.HTTPX_SEND_REQUEST.value,
)


def patch() -> None:
    _patcher.patch()


def unpatch() -> None:
    _patcher.unpatch()
