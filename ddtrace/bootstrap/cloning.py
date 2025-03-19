# Support for gevent via module cloning

import os
import sys


LOADED_MODULES = frozenset(sys.modules.keys())


# Ensure we capture references to unpatched modules as early as possible
import ddtrace.internal._unpatched  # noqa


if "gevent" in sys.modules or "gevent.monkey" in sys.modules:
    import gevent.monkey  # noqa:F401

    if gevent.monkey.is_module_patched("threading"):
        import warnings

        warnings.warn(  # noqa: B028
            "Loading ddtrace after gevent.monkey.patch_all() is not supported and is "
            "likely to break the application. Use ddtrace-run to fix this, or "
            "import ddtrace.auto before calling gevent.monkey.patch_all().",
            RuntimeWarning,
        )


def cleanup_loaded_modules():
    import logging

    from ddtrace.internal.module import ModuleWatchdog  # noqa:F401
    from ddtrace.internal.module import is_module_installed
    from ddtrace.internal.utils.formats import asbool  # noqa:F401

    def drop(module_name: str) -> None:
        del sys.modules[module_name]

    MODULES_REQUIRING_CLEANUP = ("gevent",)
    do_cleanup = os.getenv("DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE", default="auto").lower()
    if do_cleanup == "auto":
        do_cleanup = any(is_module_installed(m) for m in MODULES_REQUIRING_CLEANUP)

    if not asbool(do_cleanup):
        return

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
            "typing",
            "re",  # referenced by the typing module
            "sre_constants",  # imported by re at runtime
            "logging",
            "attr",
            "google",
            "google.protobuf",  # the upb backend in >= 4.21 does not like being unloaded
            "wrapt",
        ]
    )
    for m in list(_ for _ in sys.modules if _ not in LOADED_MODULES):
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
