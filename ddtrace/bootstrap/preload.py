"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""

import os  # noqa:I001
import atexit  # noqa:I001

from ddtrace import config  # noqa:F401
from ddtrace.settings.profiling import config as profiling_config  # noqa:F401
from ddtrace.internal import forksafe  # noqa:F401
from ddtrace.internal.logger import get_logger  # noqa:F401
from ddtrace.internal.module import ModuleWatchdog  # noqa:F401
from ddtrace.internal.products import manager  # noqa:F401
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker  # noqa:F401
from ddtrace.internal.tracemethods import _install_trace_methods  # noqa:F401
from ddtrace.internal.utils.formats import asbool  # noqa:F401
from ddtrace.internal.utils.formats import parse_tags_str  # noqa:F401
from ddtrace.internal.uwsgi import check_uwsgi  # noqa:F401
from ddtrace.internal.uwsgi import uWSGIConfigError  # noqa:F401
from ddtrace.internal.uwsgi import uWSGIMasterProcess  # noqa:F401
from ddtrace.settings.crashtracker import config as crashtracker_config
from ddtrace import tracer


import typing as t

# Register operations to be performned after the preload is complete. In
# general, we might need to perform some cleanup operations after the
# initialisation of the library, while also execute some more code after that.
#  _____ ___  _________  _____ ______  _____   ___   _   _  _____
# |_   _||  \/  || ___ \|  _  || ___ \|_   _| / _ \ | \ | ||_   _|
#   | |  | .  . || |_/ /| | | || |_/ /  | |  / /_\ \|  \| |  | |
#   | |  | |\/| ||  __/ | | | ||    /   | |  |  _  || . ` |  | |
#  _| |_ | |  | || |    \ \_/ /| |\ \   | |  | | | || |\  |  | |
#  \___/ \_|  |_/\_|     \___/ \_| \_|  \_/  \_| |_/\_| \_/  \_/
# Do not register any functions that import ddtrace modules that have not been
# imported yet.
post_preload = []


def register_post_preload(func: t.Callable) -> None:
    post_preload.append(func)


log = get_logger(__name__)

# DEV: We want to start the crashtracker as early as possible
if crashtracker_config.enabled:
    log.debug("crashtracking enabled via environment variable")
    try:
        from ddtrace.internal.core import crashtracking

        crashtracking.start()
    except Exception:
        log.error("failed to enable crashtracking", exc_info=True)


if profiling_config.enabled:
    log.debug("profiler enabled via environment variable")
    try:
        import ddtrace.profiling.auto  # noqa: F401
    except Exception:
        log.error("failed to enable profiling", exc_info=True)

if config._runtime_metrics_enabled:
    RuntimeWorker.enable()

if asbool(os.getenv("DD_IAST_ENABLED", False)):
    """
    This is the entry point for the IAST instrumentation. `enable_iast_propagation` is called on patch_all function
    too but patch_all depends of DD_TRACE_ENABLED environment variable. This is the reason why we need to call it
    here and it's not a duplicate call due to `enable_iast_propagation` has a global variable to avoid multiple calls.
    """
    from ddtrace.appsec._iast import enable_iast_propagation

    enable_iast_propagation()

if config._otel_enabled:

    @ModuleWatchdog.after_module_imported("opentelemetry.trace")
    def _(_):
        from opentelemetry.trace import set_tracer_provider

        from ddtrace.opentelemetry import TracerProvider

        set_tracer_provider(TracerProvider())


if config._llmobs_enabled:
    from ddtrace.llmobs import LLMObs

    LLMObs.enable()

if asbool(os.getenv("DD_TRACE_ENABLED", default=True)):
    from ddtrace import patch_all

    @register_post_preload
    def _():
        # We need to clean up after we have imported everything we need from
        # ddtrace, but before we register the patch-on-import hooks for the
        # integrations.
        modules_to_patch = os.getenv("DD_PATCH_MODULES")
        modules_to_str = parse_tags_str(modules_to_patch)
        modules_to_bool = {k: asbool(v) for k, v in modules_to_str.items()}
        patch_all(**modules_to_bool)

    if config.trace_methods:
        _install_trace_methods(config.trace_methods)

if "DD_TRACE_GLOBAL_TAGS" in os.environ:
    env_tags = os.getenv("DD_TRACE_GLOBAL_TAGS")
    tracer.set_tags(parse_tags_str(env_tags))


@register_post_preload
def _():
    tracer._generate_diagnostic_logs()


def do_products():
    # Start all products
    manager.start_products()

    # Restart products on fork
    forksafe.register(manager.restart_products)

    # Stop all products on exit
    atexit.register(manager.exit_products)


try:
    check_uwsgi(worker_callback=forksafe.ddtrace_after_in_child)
except uWSGIMasterProcess:
    # We are in the uWSGI master process, we should handle products in the
    # post-fork callback
    @forksafe.register
    def _():
        do_products()
        forksafe.unregister(_)

except uWSGIConfigError:
    log.error("uWSGI configuration error", exc_info=True)
except Exception:
    log.exception("Failed to check uWSGI configuration")

else:
    do_products()

# Post preload operations
register_post_preload(manager.post_preload_products)
