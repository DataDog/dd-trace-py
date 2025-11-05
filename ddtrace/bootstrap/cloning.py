import logging
import os
import sys
import warnings

from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import is_module_installed
from ddtrace.internal.utils.formats import asbool  # noqa:F401


MODULES_REQUIRING_CLEANUP = ("gevent",)


enabled = (
    any(is_module_installed(m) for m in MODULES_REQUIRING_CLEANUP)
    if (_unload_modules := os.getenv("DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE", default="auto").lower()) == "auto"
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

    # We need to import these modules to make sure they grab references to the
    # right modules before we start unloading stuff.
    import ddtrace.internal.http  # noqa
    import ddtrace.internal.uds  # noqa

    # Unload all the modules that we have imported, except for the ddtrace one.
    # NB: this means that every `import threading` anywhere in `ddtrace/` code
    # uses a copy of that module that is distinct from the copy that user code
    # gets when it does `import threading`. The same applies to every module
    # not in `KEEP_MODULES`.
    KEEP_MODULES = frozenset(
        [
            "atexit",
            "copyreg",  # pickling issues for tracebacks with gevent
            "ddtrace",
            "concurrent",
            "importlib._bootstrap",  # special import that must not be unloaded
            "typing",
            "_operator",  # pickling issues with typing module
            "re",  # referenced by the typing module
            "sre_constants",  # imported by re at runtime
            "logging",
            "attr",
            "google",
            "google.protobuf",  # the upb backend in >= 4.21 does not like being unloaded
            "wrapt",
            "bytecode",  # needed by before-fork hooks
        ]
    )
    for m in list(_ for _ in sys.modules if _ not in ddtrace.LOADED_MODULES):
        if any(m == _ or m.startswith(_ + ".") for _ in KEEP_MODULES):
            continue

        drop(m)

    # TODO: The better strategy is to identify the core modules in LOADED_MODULES
    # that should not be unloaded, and then unload as much as possible.
    UNLOAD_MODULES = frozenset(
        [
            # imported in Python >= 3.10 and patched by gevent
            "time",
            # we cannot unload the whole concurrent hierarchy, but this
            # submodule makes use of threading so it is critical to unload when
            # gevent is used.
            "concurrent.futures",
            # We unload the threading module in case it was imported by
            # CPython on boot.
            "threading",
            "_thread",
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
        logging.threading = threading

    # Do module cloning only once
    enabled = False
