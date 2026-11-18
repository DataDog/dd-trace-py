import pylibmc

from ddtrace import config
from ddtrace.ext import memcached
from ddtrace.internal.schema import schematize_service_name

from .client import TracedClient


# Original Client class
_Client = pylibmc.Client


config._add(  # type: ignore[no-untyped-call]  # Integration configuration registration is not typed yet.
    "pylibmc",
    dict(_default_service=schematize_service_name(memcached.SERVICE)),
)


def get_version() -> str:
    return getattr(pylibmc, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"pylibmc": ">=1.6.2"}


def patch() -> None:
    if getattr(pylibmc, "_datadog_patch", False):
        return

    pylibmc._datadog_patch = True
    pylibmc.Client = TracedClient


def unpatch() -> None:
    if getattr(pylibmc, "_datadog_patch", False):
        pylibmc._datadog_patch = False
    pylibmc.Client = _Client
