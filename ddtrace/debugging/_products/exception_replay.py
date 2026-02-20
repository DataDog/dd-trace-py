import enum
from typing import Any

from ddtrace.debugging._config import er_config as config


# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload() -> None:
    pass


def start() -> None:
    from ddtrace.debugging._exception.replay import SpanExceptionHandler

    SpanExceptionHandler.enable()


def enabled() -> bool:
    return config.enabled


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    from ddtrace.debugging._exception.replay import SpanExceptionHandler

    SpanExceptionHandler.disable()


def at_exit(join: bool = False) -> None:
    stop(join=join)


class APMCapabilities(enum.IntFlag):
    APM_TRACING_ENABLE_EXCEPTION_REPLAY = 1 << 39


def apm_tracing_rc(lib_config: Any, _config: Any) -> None:
    if (enabled := lib_config.get("exception_replay_enabled")) is not None:
        should_start = (config.spec.enabled.full_name not in config.source or config.enabled) and enabled
        start() if should_start else stop()
