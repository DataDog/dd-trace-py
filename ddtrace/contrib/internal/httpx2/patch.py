import httpx2

from ddtrace import config
from ddtrace.contrib.internal.httpx.common import HttpxPatcher
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


def get_version() -> str:
    return getattr(httpx2, "__version__", "")


config._add(  # type: ignore[no-untyped-call]
    "httpx2",
    {
        "distributed_tracing": asbool(env.get("DD_HTTPX2_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(env.get("DD_HTTPX2_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
    },
)

_patcher = HttpxPatcher(httpx2, config.httpx2)


def _supported_versions() -> dict[str, str]:
    return {"httpx2": ">=2.0"}


def patch() -> None:
    _patcher.patch()


def unpatch() -> None:
    _patcher.unpatch()
