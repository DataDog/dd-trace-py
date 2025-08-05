"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""

import typing as t

from ddtrace import config  # noqa:F401
from ddtrace.internal.logger import get_logger  # noqa:F401
from ddtrace.internal.module import ModuleWatchdog  # noqa:F401
from ddtrace.internal.products import manager  # noqa:F401
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker  # noqa:F401
from ddtrace.settings.crashtracker import config as crashtracker_config
from ddtrace.settings.profiling import config as profiling_config  # noqa:F401
from ddtrace.trace import tracer


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

# Run the product manager protocol
manager.run_protocol()

# Post preload operations
register_post_preload(manager.post_preload_products)


# TODO: Migrate the following product logic to the new product plugin interface

# DEV: We want to start the crashtracker as early as possible
if crashtracker_config.enabled:
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

if config._otel_enabled:

    @ModuleWatchdog.after_module_imported("opentelemetry.trace")
    def _(_):
        from opentelemetry.trace import set_tracer_provider

        from ddtrace.opentelemetry import TracerProvider

        set_tracer_provider(TracerProvider())


if config._otel_metrics_enabled:

    @ModuleWatchdog.after_module_imported("opentelemetry.metrics")
    def _otel_metrics(_):
        from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

        set_otel_meter_provider()


if config._llmobs_enabled:
    from ddtrace.llmobs import LLMObs

    LLMObs.enable(_auto=True)


@register_post_preload
def _():
    tracer._generate_diagnostic_logs()
