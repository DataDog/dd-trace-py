from typing import Any

from ddtrace.debugging._config import er_config as config
from ddtrace.internal.native import RemoteConfigCapabilities


# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload() -> None:
    pass


def start() -> None:
    from ddtrace.debugging._exception.replay import SpanExceptionHandler

    SpanExceptionHandler.enable()


def enabled() -> bool:
    # TODO: remove bool() cast once envier mypy plugin resolves config
    # attributes to their declared types
    return bool(config.enabled)


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    from ddtrace.debugging._exception.replay import SpanExceptionHandler

    SpanExceptionHandler.disable()


APMCapabilities = (RemoteConfigCapabilities.ApmTracingEnableExceptionReplay,)


def apm_tracing_rc(lib_config: Any, _config: Any) -> None:
    if (enabled := lib_config.get("exception_replay_enabled")) is not None:
        should_start = (config.spec.enabled.full_name not in config.source or config.enabled) and enabled
        start() if should_start else stop()
