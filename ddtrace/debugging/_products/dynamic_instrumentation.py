import enum
from typing import Any

from ddtrace.debugging._import import DebuggerModuleWatchdog
from ddtrace.internal.settings.dynamic_instrumentation import config


# We need to install this on start-up because if DI gets enabled remotely
# we won't be able to capture many of the code objects from the modules
# that are already loaded.
DebuggerModuleWatchdog.install()

requires = ["remote-configuration"]


def post_preload() -> None:
    pass


def start() -> None:
    from ddtrace.debugging import DynamicInstrumentation

    DynamicInstrumentation.enable()


def enabled() -> bool:
    return config.enabled


def before_fork() -> None:
    # We need to make sure that each process shares the same RC data connector
    import ddtrace.debugging._probe.remoteconfig  # noqa


def restart(join: bool = False) -> None:
    # Nothing to do
    pass


def stop(join: bool = False) -> None:
    from ddtrace.debugging import DynamicInstrumentation

    DynamicInstrumentation.disable(join=join)


def at_exit(join: bool = False) -> None:
    stop(join=join)


class APMCapabilities(enum.IntFlag):
    APM_TRACING_ENABLE_DYNAMIC_INSTRUMENTATION = 1 << 38


def apm_tracing_rc(lib_config: Any, _config: Any) -> None:
    if (enabled := lib_config.get("dynamic_instrumentation_enabled")) is not None:
        should_start = (config.spec.enabled.full_name not in config.source or config.enabled) and enabled
        start() if should_start else stop()
