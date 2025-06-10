import enum

from ddtrace.debugging._config import er_config as config


# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload():
    pass


def _start():
    from ddtrace.debugging._exception.replay import SpanExceptionHandler

    SpanExceptionHandler.enable()


def start():
    if config.enabled:
        _start()


def restart(join=False):
    pass


def _stop():
    from ddtrace.debugging._exception.replay import SpanExceptionHandler

    SpanExceptionHandler.disable()


def stop(join=False):
    if config.enabled:
        _stop()


def at_exit(join=False):
    stop(join=join)


class APMCapabilities(enum.IntFlag):
    APM_TRACING_ENABLE_EXCEPTION_REPLAY = 1 << 39


def apm_tracing_rc(lib_config, _config):
    if (enabled := lib_config.get("exception_replay_enabled")) is not None:
        should_start = (config.spec.enabled.full_name not in config.source or config.enabled) and enabled
        _start() if should_start else _stop()
