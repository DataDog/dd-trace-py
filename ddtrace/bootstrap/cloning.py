import logging
import sys
import warnings

from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import is_module_installed
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool  # noqa:F401


MODULES_REQUIRING_CLEANUP = ("gevent",)

enabled = (
    any(is_module_installed(m) for m in MODULES_REQUIRING_CLEANUP)
    if (_unload_modules := env.get("DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE", default="auto").lower()) == "auto"
    else asbool(_unload_modules)
)

if "gevent" in sys.modules or "gevent.monkey" in sys.modules:
    import gevent.monkey  # noqa:F401

    if gevent.monkey.is_module_patched("threading"):
        warnings.warn(  # noqa: B028
            "Loading ddtrace after gevent.monkey.patch_all() is not supported and is "
            "likely to break the application. Use ddtrace-run to fix this, or "
            "import ddtrace.auto before calling gevent.monkey.patch_all().",
            RuntimeWarning,
        )


def cleanup_loaded_modules() -> None:
    global enabled

    if not enabled:
        return

    def drop(module_name: str) -> None:
        module = sys.modules.get(module_name)
        # Don't delete modules that are currently being imported (they might be None or incomplete)
        # or that don't exist. This can happen when pytest's assertion rewriter is importing modules
        # that themselves import ddtrace.auto, which triggers this cleanup during the import process.
        if module is None:
            return
        # Skip modules that don't have a __spec__ attribute yet (still being imported)
        if not hasattr(module, "__spec__"):
            return
        # Check if the module is currently being initialized
        # During import, __spec__._initializing is True
        spec = getattr(module, "__spec__", None)
        if spec is not None and getattr(spec, "_initializing", False):
            return
        del sys.modules[module_name]

    # We only need to unload the modules that gevent monkey-patches in place, plus a
    # few that hold references into them. gevent patches these copies after ddtrace has
    # loaded, so application code must re-import fresh copies while ddtrace keeps the
    # pre-patch ones it imported (e.g. the profiler's real-thread tracking). Modules
    # that gevent does not touch are left shared, which avoids the fragile allowlist of
    # unrelated modules (typing, dataclasses, asyncio, ...) the old broad sweep needed.
    # ``os`` is patched by gevent too but is imported on interpreter boot and only has
    # fork hooks replaced, so it is safe to leave shared.
    UNLOAD_MODULES = frozenset(
        [
            "time",
            "threading",
            "_thread",
            "socket",
            # ssl.SSLContext recurses infinitely if patched twice, so a clean re-import
            # is required when gevent is installed.
            "ssl",
            "select",
            "selectors",
            "queue",
            "signal",
            "subprocess",
            # uses threading; must be re-imported against the gevent-patched copy.
            "concurrent.futures",
            # reprlib does `from _thread import get_ident` at module level; unloading it
            # ensures a fresh re-import binds the correct get_ident, keeping it picklable.
            "reprlib",
        ]
    )
    for u in UNLOAD_MODULES:
        for m in list(sys.modules):
            if m == u or m.startswith(u + "."):
                drop(m)

    # Because we are not unloading it, the logging module requires a reference
    # to the newly imported threading module to allow it to retrieve the correct
    # thread object information, like the thread name. We register a post-import
    # hook on the threading module to perform this update.
    @ModuleWatchdog.after_module_imported("threading")
    def _(threading):
        logging.threading = threading  # type: ignore[attr-defined]

    # Do module cloning only once
    enabled = False
