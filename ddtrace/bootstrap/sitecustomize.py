"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""
#  _____ ___  _________  _____ ______  _____   ___   _   _  _____
# |_   _||  \/  || ___ \|  _  || ___ \|_   _| / _ \ | \ | ||_   _|
#   | |  | .  . || |_/ /| | | || |_/ /  | |  / /_\ \|  \| |  | |
#   | |  | |\/| ||  __/ | | | ||    /   | |  |  _  || . ` |  | |
#  _| |_ | |  | || |    \ \_/ /| |\ \   | |  | | | || |\  |  | |
#  \___/ \_|  |_/\_|     \___/ \_| \_|  \_/  \_| |_/\_| \_/  \_/
# DO NOT MODIFY THIS FILE!
# Only do so if you know what you're doing. This file contains boilerplate code
# to allow injecting a custom sitecustomize.py file into the Python process to
# perform the correct initialisation for the library. All the actual
# initialisation logic should be placed in preload.py.
from ddtrace import LOADED_MODULES  # isort:skip

import logging  # noqa
import os  # noqa
import sys
import warnings  # noqa

from ddtrace import config  # noqa
from ddtrace._logger import _configure_log_injection
from ddtrace.internal.logger import get_logger  # noqa
from ddtrace.internal.module import ModuleWatchdog  # noqa
from ddtrace.internal.module import find_loader  # noqa
from ddtrace.internal.utils.formats import asbool  # noqa

# Debug mode from the tracer will do the same here, so only need to do this otherwise.
if config.logs_injection:
    _configure_log_injection()


log = get_logger(__name__)


if "gevent" in sys.modules or "gevent.monkey" in sys.modules:
    import gevent.monkey  # noqa

    if gevent.monkey.is_module_patched("threading"):
        warnings.warn(  # noqa: B028
            "Loading ddtrace after gevent.monkey.patch_all() is not supported and is "
            "likely to break the application. Use ddtrace-run to fix this, or "
            "import ddtrace.auto before calling gevent.monkey.patch_all().",
            RuntimeWarning,
        )


def is_module_installed(module_name):
    return find_loader(module_name) is not None


def cleanup_loaded_modules():
    def drop(module_name):
        # type: (str) -> None
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
        ]
    )
    for m in list(_ for _ in sys.modules if _ not in LOADED_MODULES):
        if any(m == _ or m.startswith(_ + ".") for _ in KEEP_MODULES):
            continue

        drop(m)

    # TODO: The better strategy is to identify the core modues in LOADED_MODULES
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


try:
    import ddtrace.bootstrap.preload as preload  # Perform the actual initialisation

    cleanup_loaded_modules()

    # Check for and import any sitecustomize that would have normally been used
    # had ddtrace-run not been used.
    bootstrap_dir = os.path.dirname(__file__)
    if bootstrap_dir in sys.path:
        index = sys.path.index(bootstrap_dir)
        del sys.path[index]

        # NOTE: this reference to the module is crucial in Python 2.
        # Without it the current module gets gc'd and all subsequent references
        # will be `None`.
        ddtrace_sitecustomize = sys.modules["sitecustomize"]
        del sys.modules["sitecustomize"]

        # Cache this module under it's fully qualified package name
        if "ddtrace.bootstrap.sitecustomize" not in sys.modules:
            sys.modules["ddtrace.bootstrap.sitecustomize"] = ddtrace_sitecustomize

        try:
            import sitecustomize  # noqa
        except ImportError:
            # If an additional sitecustomize is not found then put the ddtrace
            # sitecustomize back.
            log.debug("additional sitecustomize not found")
            sys.modules["sitecustomize"] = ddtrace_sitecustomize
        else:
            log.debug("additional sitecustomize found in: %s", sys.path)
        finally:
            # Always reinsert the ddtrace bootstrap directory to the path so
            # that introspection and debugging the application makes sense.
            # Note that this does not interfere with imports since a user
            # sitecustomize, if it exists, will be imported.
            sys.path.insert(index, bootstrap_dir)
    else:
        try:
            import sitecustomize  # noqa
        except ImportError:
            log.debug("additional sitecustomize not found")
        else:
            log.debug("additional sitecustomize found in: %s", sys.path)

    config._ddtrace_bootstrapped = True
    # Loading status used in tests to detect if the `sitecustomize` has been
    # properly loaded without exceptions. This must be the last action in the module
    # when the execution ends with a success.
    loaded = True

    for f in preload.post_preload:
        f()

except Exception:
    loaded = False
    log.warning("error configuring Datadog tracing", exc_info=True)
