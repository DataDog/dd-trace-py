"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""
import sys


MODULES_LOADED_AT_STARTUP = frozenset(sys.modules.keys())
MODULES_THAT_TRIGGER_CLEANUP_WHEN_INSTALLED = ("gevent",)


import os  # noqa


"""
The following modules cause problems when being unloaded/reloaded in module cloning.
Notably, unloading the atexit module will remove all registered hooks which we use for cleaning up on tracer shutdown.
The other listed modules internally maintain some state that does not coexist well if reloaded.
"""
MODULES_TO_NOT_CLEANUP = {"atexit", "asyncio", "attr", "concurrent", "ddtrace", "logging"}
if sys.version_info < (3, 7):
    MODULES_TO_NOT_CLEANUP |= {"typing"}  # required by older versions of Python
if sys.version_info <= (2, 7):
    MODULES_TO_NOT_CLEANUP |= {"encodings", "codecs"}
    import imp

    _unloaded_modules = []

    def is_installed(module_name):
        try:
            imp.find_module(module_name)
        except ImportError:
            return False
        return True


else:
    import importlib

    def is_installed(module_name):
        return importlib.util.find_spec(module_name)


def should_cleanup_loaded_modules():
    dd_unload_sitecustomize_modules = os.getenv("DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE", default="0").lower()
    if dd_unload_sitecustomize_modules not in ("1", "auto"):
        return False
    elif dd_unload_sitecustomize_modules == "auto" and not any(
        is_installed(module_name) for module_name in MODULES_THAT_TRIGGER_CLEANUP_WHEN_INSTALLED
    ):
        return False
    return True


def cleanup_loaded_modules(aggressive=False):
    """
    "Aggressive" here means "cleanup absolutely every module that has been loaded since startup".
    Non-aggressive cleanup entails leaving untouched certain modules
    This distinction is necessary because this function is used both to prepare for gevent monkeypatching
    (requiring aggressive cleanup) and to implement "module cloning" (requiring non-aggressive cleanup)
    """
    # Figuring out modules_loaded_since_startup is necessary because sys.modules has more in it than just what's in
    # import statements in this file, and unloading some of them can break the interpreter.
    modules_loaded_since_startup = set(_ for _ in sys.modules if _ not in MODULES_LOADED_AT_STARTUP)
    # Unload all the modules that we have imported, except for ddtrace and a few
    # others that don't like being cloned.
    # Doing so will allow ddtrace to continue using its local references to modules unpatched by
    # gevent, while avoiding conflicts with user-application code potentially running
    # `gevent.monkey.patch_all()` and thus gevent-patched versions of the same modules.
    for module_name in modules_loaded_since_startup:
        if aggressive:
            del sys.modules[module_name]
            continue

        for module_to_not_cleanup in MODULES_TO_NOT_CLEANUP:
            if module_name == module_to_not_cleanup:
                break
            elif module_name.startswith("%s." % module_to_not_cleanup):
                break
        else:
            del sys.modules[module_name]
    # Some versions of CPython import the time module during interpreter startup, which needs to be unloaded.
    if "time" in sys.modules:
        del sys.modules["time"]


will_run_module_cloning = should_cleanup_loaded_modules()
if not will_run_module_cloning:
    # Perform gevent patching as early as possible in the application before
    # importing more of the library internals.
    if os.environ.get("DD_GEVENT_PATCH_ALL", "false").lower() in ("true", "1"):
        # successfully running `gevent.monkey.patch_all()` this late into
        # sitecustomize requires aggressive module unloading beforehand.
        # gevent's documentation strongly warns against calling monkey.patch_all() anywhere other
        # than the first line of the program. since that's what we're doing here,
        # we cleanup aggressively beforehand to replicate the conditions at program start
        # as closely as possible.
        cleanup_loaded_modules(aggressive=True)
        import gevent.monkey

        gevent.monkey.patch_all()

import logging  # noqa
import os  # noqa
from typing import Any  # noqa
from typing import Dict  # noqa

from ddtrace import config  # noqa
from ddtrace.debugging._config import config as debugger_config  # noqa
from ddtrace.internal.logger import get_logger  # noqa
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker  # noqa
from ddtrace.internal.utils.formats import asbool  # noqa
from ddtrace.internal.utils.formats import parse_tags_str  # noqa
from ddtrace.tracer import DD_LOG_FORMAT  # noqa
from ddtrace.tracer import debug_mode  # noqa
from ddtrace.vendor.debtcollector import deprecate  # noqa


if config.logs_injection:
    # immediately patch logging if trace id injected
    from ddtrace import patch

    patch(logging=True)


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

    if asbool(os.getenv("DD_IAST_ENABLED", False)):
        from ddtrace.appsec.iast._ast.ast_patching import _should_iast_patch
        from ddtrace.appsec.iast._loader import _exec_iast_patched_module
        from ddtrace.internal.module import ModuleWatchdog

        ModuleWatchdog.register_pre_exec_module_hook(_should_iast_patch, _exec_iast_patched_module)

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

    # We need to clean up after we have imported everything we need from
    # ddtrace, but before we register the patch-on-import hooks for the
    # integrations. This is because registering a hook for a module
    # that is already imported causes the module to be patched immediately.
    # So if we unload the module after registering hooks, we effectively
    # remove the patching, thus breaking the tracer integration.
    if will_run_module_cloning:
        cleanup_loaded_modules()
    if trace_enabled:
        update_patched_modules()
        from ddtrace import patch_all

        patch_all(**EXTRA_PATCHED_MODULES)

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
