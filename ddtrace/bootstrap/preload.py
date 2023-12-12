"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""
import os  # noqa:I001

from ddtrace import config  # noqa:F401
from ddtrace.debugging._config import di_config  # noqa:F401
from ddtrace.debugging._config import ed_config  # noqa:F401
from ddtrace.settings.profiling import config as profiling_config  # noqa:F401
from ddtrace.internal.logger import get_logger  # noqa:F401
from ddtrace.internal.module import ModuleWatchdog  # noqa:F401
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker  # noqa:F401
from ddtrace.internal.tracemethods import _install_trace_methods  # noqa:F401
from ddtrace.internal.utils.formats import asbool  # noqa:F401
from ddtrace.internal.utils.formats import parse_tags_str  # noqa:F401
from ddtrace.settings.asm import config as asm_config  # noqa:F401
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


if profiling_config.enabled:
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

if config._remote_config_enabled:
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.enable()
    config.enable_remote_configuration()

if asm_config._asm_enabled or config._remote_config_enabled:
    from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

    enable_appsec_rc()

if config._otel_enabled:

    @ModuleWatchdog.after_module_imported("opentelemetry.trace")
    def _(_):
        from opentelemetry.trace import set_tracer_provider

        from ddtrace.opentelemetry import TracerProvider

        set_tracer_provider(TracerProvider())


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
