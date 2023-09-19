"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""
from ddtrace import LOADED_MODULES  # isort:skip

import logging  # noqa
import os  # noqa
import sys
import warnings  # noqa

from ddtrace import config  # noqa
from ddtrace._logger import _configure_log_injection
from ddtrace.debugging._config import di_config  # noqa
from ddtrace.debugging._config import ed_config  # noqa
from ddtrace.internal.compat import PY2  # noqa
from ddtrace.internal.logger import get_logger  # noqa
from ddtrace.internal.module import ModuleWatchdog  # noqa
from ddtrace.internal.module import find_loader  # noqa
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker  # noqa
from ddtrace.internal.utils.formats import asbool  # noqa
from ddtrace.internal.utils.formats import parse_tags_str  # noqa


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

    # Because we are not unloading it, the logging module requires a reference
    # to the newly imported threading module to allow it to retrieve the correct
    # thread object information, like the thread name. We register a post-import
    # hook on the threading module to perform this update.
    @ModuleWatchdog.after_module_imported("threading")
    def _(threading):
        logging.threading = threading


try:
    from ddtrace import tracer

    profiling = asbool(os.getenv("DD_PROFILING_ENABLED", False))

    if profiling:
        log.debug("profiler enabled via environment variable")
        import ddtrace.profiling.auto  # noqa: F401

    if di_config.enabled or ed_config.enabled:
        from ddtrace.debugging import DynamicInstrumentation

        DynamicInstrumentation.enable()

    if config._runtime_metrics_enabled:
        RuntimeWorker.enable()

    if asbool(os.getenv("DD_IAST_ENABLED", False)):

        from ddtrace.appsec._iast._utils import _is_python_version_supported

        if _is_python_version_supported():

            from ddtrace.appsec._iast._ast.ast_patching import _should_iast_patch
            from ddtrace.appsec._iast._loader import _exec_iast_patched_module

            ModuleWatchdog.register_pre_exec_module_hook(_should_iast_patch, _exec_iast_patched_module)

    if asbool(os.getenv("DD_TRACE_ENABLED", default=True)):
        from ddtrace import patch_all

        # We need to clean up after we have imported everything we need from
        # ddtrace, but before we register the patch-on-import hooks for the
        # integrations.
        cleanup_loaded_modules()
        modules_to_patch = os.getenv("DD_PATCH_MODULES")
        modules_to_str = parse_tags_str(modules_to_patch)
        modules_to_bool = {k: asbool(v) for k, v in modules_to_str.items()}
        patch_all(**modules_to_bool)
    else:
        cleanup_loaded_modules()

    # Only the import of the original sitecustomize.py is allowed after this
    # point.

    if "DD_TRACE_GLOBAL_TAGS" in os.environ:
        env_tags = os.getenv("DD_TRACE_GLOBAL_TAGS")
        tracer.set_tags(parse_tags_str(env_tags))

    if sys.version_info >= (3, 7) and config._otel_enabled:
        from opentelemetry.trace import set_tracer_provider

        from ddtrace.opentelemetry import TracerProvider

        set_tracer_provider(TracerProvider())

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

    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.enable()

    if config._appsec_enabled or config._remote_config_enabled:
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc()

    config._ddtrace_bootstrapped = True
    # Loading status used in tests to detect if the `sitecustomize` has been
    # properly loaded without exceptions. This must be the last action in the module
    # when the execution ends with a success.
    loaded = True
    tracer._generate_diagnostic_logs()
except Exception:
    loaded = False
    log.warning("error configuring Datadog tracing", exc_info=True)
