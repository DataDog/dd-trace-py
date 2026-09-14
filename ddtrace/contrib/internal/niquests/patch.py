import niquests

from ddtrace import config
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


config._add(  # type: ignore[no-untyped-call]
    "niquests",
    {
        "distributed_tracing": asbool(env.get("DD_NIQUESTS_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(env.get("DD_NIQUESTS_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        # The schema function is selected dynamically and has no stable callable type.
        "_default_service": schematize_service_name("niquests"),  # type: ignore[operator]
    },
)


def get_version() -> str:
    return str(getattr(niquests, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"niquests": ">=3.0"}


def patch() -> None:
    pass


def unpatch() -> None:
    pass
