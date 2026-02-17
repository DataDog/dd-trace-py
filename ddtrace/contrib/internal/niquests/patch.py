import os

import niquests
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.formats import asbool

from .connection import _wrap_send_async
from .connection import _wrap_send_sync
from .session import TracedAsyncSession
from .session import TracedSession


config._add(
    "niquests",
    {
        "distributed_tracing": asbool(os.getenv("DD_NIQUESTS_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(os.getenv("DD_NIQUESTS_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        "_default_service": schematize_service_name("niquests"),
    },
)

_w(TracedSession, "send", _wrap_send_sync)
_w(TracedAsyncSession, "send", _wrap_send_async)

Pin(_config=config.niquests).onto(TracedSession)
Pin(_config=config.niquests).onto(TracedAsyncSession)


def get_version() -> str:
    return getattr(niquests, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"niquests": ">=3.17.0"}


def patch():
    if getattr(niquests, "__datadog_patch", False):
        return
    niquests.__datadog_patch = True

    _w("niquests", "Session.send", _wrap_send_sync)
    _w("niquests", "AsyncSession.send", _wrap_send_async)
    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_request

        _w("niquests", "Session.request", _wrap_request)
    Pin(_config=config.niquests).onto(niquests.Session)
    Pin(_config=config.niquests).onto(niquests.AsyncSession)


def unpatch():
    if not getattr(niquests, "__datadog_patch", False):
        return
    niquests.__datadog_patch = False

    try:
        _u(niquests.Session, "request")
    except AttributeError:
        pass

    try:
        _u(niquests.Session, "send")
    except AttributeError:
        pass

    try:
        _u(niquests.AsyncSession, "send")
    except AttributeError:
        pass
