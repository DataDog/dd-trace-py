import enum
from typing import Any

from ddtrace.debugging._config import er_config as config


# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload() -> None:
    pass


def _start() -> None:
    from ddtrace.debugging._exception.replay import SpanExceptionHandler

    SpanExceptionHandler.enable()


def start() -> None:
    if config.enabled:
        _start()


def restart(join: bool = False) -> None:
    pass


def _stop() -> None:
    from ddtrace.debugging._exception.replay import SpanExceptionHandler

    SpanExceptionHandler.disable()


def stop(join: bool = False) -> None:
    if config.enabled:
        _stop()


def at_exit(join: bool = False) -> None:
    stop(join=join)


class APMCapabilities(enum.IntFlag):
    APM_TRACING_ENABLE_EXCEPTION_REPLAY = 1 << 39


def apm_tracing_rc(lib_config: Any, _config: Any) -> None:
    if (enabled := lib_config.get("exception_replay_enabled")) is not None:
        should_start = (config.spec.enabled.full_name not in config.source or config.enabled) and enabled
        _start() if should_start else _stop()
