"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""

import os  # noqa:I001

from ddtrace import config  # noqa:F401
from ddtrace.internal.logger import get_logger  # noqa:F401
from ddtrace.internal.module import ModuleWatchdog  # noqa:F401
from ddtrace.internal.products import manager  # noqa:F401
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker  # noqa:F401
from ddtrace.internal.tracemethods import _install_trace_methods  # noqa:F401
from ddtrace.internal.utils.formats import asbool  # noqa:F401
from ddtrace.internal.utils.formats import parse_tags_str  # noqa:F401
from ddtrace.settings.crashtracker import config as crashtracker_config
from ddtrace.trace import tracer


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

# Run the product manager protocol
manager.run_protocol()

# Post preload operations
register_post_preload(manager.post_preload_products)


# TODO: Migrate the following product logic to the new product plugin interface

# DEV: We want to start the crashtracker as early as possible
if crashtracker_config.enabled:
    log.debug("crashtracking enabled via environment variable")
    try:
        from ddtrace.internal.core import crashtracking

        crashtracking.start()
    except Exception:
        log.error("failed to enable crashtracking", exc_info=True)

if config._runtime_metrics_enabled:
    RuntimeWorker.enable()

if config._otel_enabled:

    @ModuleWatchdog.after_module_imported("opentelemetry.trace")
    def _(_):
        from opentelemetry.trace import set_tracer_provider

        from ddtrace.opentelemetry import TracerProvider

        set_tracer_provider(TracerProvider())


if config._llmobs_enabled:
    from ddtrace.llmobs import LLMObs

    LLMObs.enable(_auto=True)

if asbool(os.getenv("DD_TRACE_ENABLED", default=True)):
    from ddtrace._monkey import _patch_all

    @register_post_preload
    def _():
        # We need to clean up after we have imported everything we need from
        # ddtrace, but before we register the patch-on-import hooks for the
        # integrations.
        modules_to_patch = os.getenv("DD_PATCH_MODULES")
        modules_to_str = parse_tags_str(modules_to_patch)
        modules_to_bool = {k: asbool(v) for k, v in modules_to_str.items()}
        _patch_all(**modules_to_bool)

    if config._trace_methods:
        _install_trace_methods(config._trace_methods)

if "DD_TRACE_GLOBAL_TAGS" in os.environ:
    env_tags = os.getenv("DD_TRACE_GLOBAL_TAGS")
    tracer.set_tags(parse_tags_str(env_tags))


@register_post_preload
def _():
    tracer._generate_diagnostic_logs()
