"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""
import sys


MODULES_LOADED_AT_STARTUP = frozenset(sys.modules.keys())

import logging  # noqa
import os  # noqa
from typing import Any  # noqa
from typing import Dict  # noqa

from ddtrace import config  # noqa
from ddtrace.debugging._config import config as debugger_config  # noqa
from ddtrace.internal.compat import PY2  # noqa
from ddtrace.internal.logger import get_logger  # noqa
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker  # noqa
from ddtrace.internal.utils.formats import asbool  # noqa
from ddtrace.internal.utils.formats import parse_tags_str  # noqa
from ddtrace.tracer import DD_LOG_FORMAT  # noqa
from ddtrace.tracer import debug_mode  # noqa
from ddtrace.vendor.debtcollector import deprecate  # noqa


if sys.version_info < (3, 1):
    import imp
else:
    import importlib


# DEV: Once basicConfig is called here, future calls to it cannot be used to
# change the formatter since it applies the formatter to the root handler only
# upon initializing it the first time.
# See https://github.com/python/cpython/blob/112e4afd582515fcdcc0cde5012a4866e5cfda12/Lib/logging/__init__.py#L1550
# Debug mode from the tracer will do a basicConfig so only need to do this otherwise
call_basic_config = asbool(os.environ.get("DD_CALL_BASIC_CONFIG", "false"))
if not debug_mode and call_basic_config:
    deprecate(
        "ddtrace.tracer.logging.basicConfig",
        message="`logging.basicConfig()` should be called in a user's application."
        " ``DD_CALL_BASIC_CONFIG`` will be removed in a future version.",
    )
    if config.logs_injection:
        logging.basicConfig(format=DD_LOG_FORMAT)
    else:
        logging.basicConfig()

log = get_logger(__name__)

EXTRA_PATCHED_MODULES = {
    "bottle": True,
    "django": True,
    "falcon": True,
    "flask": True,
    "pylons": True,
    "pyramid": True,
}


def update_patched_modules():
    modules_to_patch = os.getenv("DD_PATCH_MODULES")
    if not modules_to_patch:
        return

    modules = parse_tags_str(modules_to_patch)
    for module, should_patch in modules.items():
        EXTRA_PATCHED_MODULES[module] = asbool(should_patch)


if PY2:
    _unloaded_modules = []


def gevent_is_installed():
    # https://stackoverflow.com/a/51491863/735204
    if sys.version_info >= (3, 4):
        return importlib.util.find_spec("gevent")
    elif sys.version_info >= (3, 3):
        return importlib.find_loader("gevent")
    elif sys.version_info >= (3, 1):
        return importlib.find_module("gevent")
    elif sys.version_info >= (2, 7):
        return imp.find_module("gevent")
    return False


def should_cleanup_loaded_modules():
    dd_unload_sitecustomize_modules = os.getenv("DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE", default="auto").lower()
    if dd_unload_sitecustomize_modules == "0":
        log.debug("skipping sitecustomize module unload because of DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE == 0")
        return False
    elif dd_unload_sitecustomize_modules not in ("1", "auto"):
        log.debug(
            "skipping sitecustomize module unload because of invalid envvar value"
            "DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE == {}".format(dd_unload_sitecustomize_modules)
        )
        return False
    elif dd_unload_sitecustomize_modules == "auto" and not gevent_is_installed():
        log.debug(
            "skipping sitecustomize module unload because DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE == auto and "
            "gevent is not installed"
        )
        return False
    return True


def cleanup_loaded_modules():
    if not should_cleanup_loaded_modules():
        return
    # Unload all the modules that we have imported, except for ddtrace and a few
    # others that don't like being cloned.
    # Doing so will allow ddtrace to continue using its local references to modules unpatched by
    # gevent, while avoiding conflicts with user-application code potentially running
    # `gevent.monkey.patch_all()` and thus gevent-patched versions of the same modules.
    for m in list(_ for _ in sys.modules if _ not in MODULES_LOADED_AT_STARTUP):
        if m.startswith("atexit"):
            continue
        if m.startswith("asyncio"):
            continue
        if m.startswith("attr"):
            continue
        if m.startswith("concurrent"):
            continue
        if m.startswith("ddtrace"):
            continue
        if m.startswith("logging"):
            continue
        if m.startswith("typing"):  # required by Python < 3.7
            continue

        if PY2:
            if m.startswith("encodings") or m.startswith("codecs"):
                continue
            # Store a reference to deleted modules to avoid them being garbage
            # collected
            _unloaded_modules.append(sys.modules[m])

        del sys.modules[m]

    # TODO: The better strategy is to identify the core modules in MODULES_LOADED_AT_STARTUP
    # that should not be unloaded, and then unload as much as possible.
    if "time" in sys.modules:
        del sys.modules["time"]


try:
    from ddtrace import tracer

    priority_sampling = os.getenv("DD_PRIORITY_SAMPLING")
    profiling = asbool(os.getenv("DD_PROFILING_ENABLED", False))

    if profiling:
        log.debug("profiler enabled via environment variable")
        import ddtrace.profiling.auto  # noqa: F401

    if debugger_config.enabled:
        from ddtrace.debugging import DynamicInstrumentation

        DynamicInstrumentation.enable()

    if asbool(os.getenv("DD_RUNTIME_METRICS_ENABLED")):
        RuntimeWorker.enable()

    opts = {}  # type: Dict[str, Any]

    dd_trace_enabled = os.getenv("DD_TRACE_ENABLED", default=True)
    if asbool(dd_trace_enabled):
        trace_enabled = True
    else:
        trace_enabled = False
        opts["enabled"] = False

    if priority_sampling:
        opts["priority_sampling"] = asbool(priority_sampling)

    if not opts:
        tracer.configure(**opts)

    if trace_enabled:
        update_patched_modules()
        from ddtrace import patch_all

        # We need to clean up after we have imported everything we need from
        # ddtrace, but before we register the patch-on-import hooks for the
        # integrations. This is because registering a hook for a module
        # that is already imported causes the module to be patched immediately.
        # So if we unload the module after registering hooks, we effectively
        # remove the patching, thus breaking the tracer integration.
        cleanup_loaded_modules()

        patch_all(**EXTRA_PATCHED_MODULES)
    else:
        cleanup_loaded_modules()

    # Only the import of the original sitecustomize.py is allowed after this
    # point.

    if "DD_TRACE_GLOBAL_TAGS" in os.environ:
        env_tags = os.getenv("DD_TRACE_GLOBAL_TAGS")
        tracer.set_tags(parse_tags_str(env_tags))

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

    # Loading status used in tests to detect if the `sitecustomize` has been
    # properly loaded without exceptions. This must be the last action in the module
    # when the execution ends with a success.
    loaded = True


except Exception:
    loaded = False
    log.warning("error configuring Datadog tracing", exc_info=True)
