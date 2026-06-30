import sys

from ddtrace import config
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.wrapping import unwrap as _u
from ddtrace.internal.wrapping import wrap as _w

from .threading import _wrap_submit


config._add(
    "futures",
    dict(_default_service=schematize_service_name("futures")),
)


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"concurrent.futures.thread": "*"}


def patch():
    """Enables Context Propagation between threads"""
    try:
        # Ensure that we get hold of the reloaded module if module cleanup was
        # performed.
        thread = sys.modules["concurrent.futures.thread"]
    except KeyError:
        import concurrent.futures.thread as thread

    if getattr(thread, "__datadog_patch", False):
        return
    thread.__datadog_patch = True

    _w(thread.ThreadPoolExecutor.submit, _wrap_submit)


def unpatch():
    """Disables Context Propagation between threads"""
    try:
        # Ensure that we get hold of the reloaded module if module cleanup was
        # performed.
        thread = sys.modules["concurrent.futures.thread"]
    except KeyError:
        return

    if not getattr(thread, "__datadog_patch", False):
        return
    thread.__datadog_patch = False

    _u(thread.ThreadPoolExecutor.submit, _wrap_submit)
