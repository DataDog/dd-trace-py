import enum

from ddtrace.settings.code_origin import config


# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload():
    pass


def _start():
    from ddtrace.debugging._origin.span import SpanCodeOriginProcessor

    SpanCodeOriginProcessor.enable()


def start():
    if config.span.enabled:
        _start()


def restart(join=False):
    pass


def _stop():
    from ddtrace.debugging._origin.span import SpanCodeOriginProcessor

    SpanCodeOriginProcessor.disable()


def stop(join=False):
    if config.span.enabled:
        _stop()


def at_exit(join=False):
    stop(join=join)


class APMCapabilities(enum.IntFlag):
    APM_TRACING_ENABLE_CODE_ORIGIN = 1 << 40


def apm_tracing_rc(lib_config, _config):
    if (enabled := lib_config.get("code_origin_enabled")) is not None:
        should_start = (config.span.spec.enabled.full_name not in config.source or config.span.enabled) and enabled
        _start() if should_start else _stop()
