"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""
from ddtrace import LOADED_MODULES  # isort:skip

import logging  # noqa
import os  # noqa
import sys
from typing import Any  # noqa
from typing import Dict  # noqa
import warnings  # noqa

from ddtrace import config  # noqa
from ddtrace.debugging._config import config as debugger_config  # noqa
from ddtrace.internal.compat import PY2  # noqa
from ddtrace.internal.logger import get_logger  # noqa
from ddtrace.internal.module import find_loader  # noqa
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

if os.environ.get("DD_GEVENT_PATCH_ALL") is not None:
    deprecate(
        "The environment variable DD_GEVENT_PATCH_ALL is deprecated and will be removed in a future version. ",
        postfix="There is no special configuration necessary to make ddtrace work with gevent if using ddtrace-run. "
        "If not using ddtrace-run, import ddtrace.auto before calling gevent.monkey.patch_all().",
        removal_version="2.0.0",
    )
if "gevent" in sys.modules or "gevent.monkey" in sys.modules:
    import gevent.monkey  # noqa

    if gevent.monkey.is_module_patched("threading"):
        warnings.warn(
            "Loading ddtrace after gevent.monkey.patch_all() is not supported and is "
            "likely to break the application. Use ddtrace-run to fix this, or "
            "import ddtrace.auto before calling gevent.monkey.patch_all().",
            RuntimeWarning,
        )


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


def is_module_installed(module_name):
    return find_loader(module_name) is not None


def cleanup_loaded_modules():
    def drop(module_name):
        # type: (str) -> None
        if PY2:
            # Store a reference to deleted modules to avoid them being garbage
            # collected
            _unloaded_modules.append(sys.modules[module_name])
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
            "asyncio",
            "concurrent",
            "typing",
            "re",  # referenced by the typing module
            "logging",
            "attr",
            "google.protobuf",  # the upb backend in >= 4.21 does not like being unloaded
        ]
    )
    if PY2:
        KEEP_MODULES_PY2 = frozenset(["encodings", "codecs", "copy_reg"])
    for m in list(_ for _ in sys.modules if _ not in LOADED_MODULES):
        if any(m == _ or m.startswith(_ + ".") for _ in KEEP_MODULES):
            continue

        if PY2:
            if any(m == _ or m.startswith(_ + ".") for _ in KEEP_MODULES_PY2):
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

        from ddtrace.appsec.iast._util import _is_python_version_supported

        if _is_python_version_supported():

            from ddtrace.appsec.iast._ast.ast_patching import _should_iast_patch
            from ddtrace.appsec.iast._loader import _exec_iast_patched_module
            from ddtrace.appsec.iast._taint_tracking import setup
            from ddtrace.internal.module import ModuleWatchdog

            setup(bytes.join, bytearray.join)

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

    if trace_enabled:
        update_patched_modules()
        from ddtrace import patch_all

        # We need to clean up after we have imported everything we need from
        # ddtrace, but before we register the patch-on-import hooks for the
        # integrations.
        cleanup_loaded_modules()

        patch_all(**EXTRA_PATCHED_MODULES)
    else:
        cleanup_loaded_modules()

    # Only the import of the original sitecustomize.py is allowed after this
    # point.

    if "DD_TRACE_GLOBAL_TAGS" in os.environ:
        env_tags = os.getenv("DD_TRACE_GLOBAL_TAGS")
        tracer.set_tags(parse_tags_str(env_tags))

    if sys.version_info >= (3, 7) and asbool(os.getenv("DD_TRACE_OTEL_ENABLED", False)):
        from opentelemetry.trace import set_tracer_provider

        from ddtrace.opentelemetry import TracerProvider

        set_tracer_provider(TracerProvider())
        # Replaces the default otel api runtime context with DDRuntimeContext
        # https://github.com/open-telemetry/opentelemetry-python/blob/v1.16.0/opentelemetry-api/src/opentelemetry/context/__init__.py#L53
        os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"

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
