from typing import Any

from ddtrace.internal.native import RemoteConfigCapabilities
from ddtrace.internal.settings.dynamic_instrumentation import config
from ddtrace.internal.utils.inspection import ModuleCodeCollector


# We need to register with the shared code collector unconditionally so that
# code objects from modules loaded before DI is enabled remotely are not
# missed.
ModuleCodeCollector.register("di")

requires = ["remote-configuration"]


def post_preload() -> None:
    pass


def start() -> None:
    from ddtrace.debugging import DynamicInstrumentation

    DynamicInstrumentation.enable()


def enabled() -> bool:
    # TODO: remove bool() cast once envier mypy plugin resolves config
    # attributes to their declared types
    return bool(config.enabled)


def before_fork() -> None:
    # We need to make sure that each process shares the same RC data connector
    import ddtrace.debugging._probe.remoteconfig  # noqa


def restart(join: bool = False) -> None:
    # Nothing to do
    pass


def stop(join: bool = False) -> None:
    from ddtrace.debugging import DynamicInstrumentation

    DynamicInstrumentation.disable(join=join)


APMCapabilities = (RemoteConfigCapabilities.ApmTracingEnableDynamicInstrumentation,)


def apm_tracing_rc(lib_config: Any, _config: Any) -> None:
    # A missing key means no record overrides this feature, so the current state
    # is left untouched. A null one means the override was removed (e.g. its
    # record was deleted on the backend), so fall back to the local value.
    if "dynamic_instrumentation_enabled" not in lib_config:
        return
    if (enabled := lib_config["dynamic_instrumentation_enabled"]) is None:
        enabled = config.parsed.enabled
    should_start = (config.spec.enabled.full_name not in config.source or config.parsed.enabled) and enabled
    start() if should_start else stop()
