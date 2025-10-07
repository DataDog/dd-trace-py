import enum

from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry
from ddtrace.internal.products import manager as product_manager
from ddtrace.settings._core import ValueSource
from ddtrace.settings.code_origin import config


CO_ENABLED = "DD_CODE_ORIGIN_FOR_SPANS_ENABLED"
DI_PRODUCT_KEY = "dynamic-instrumentation"

# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload():
    pass


def _start():
    SpanCodeOriginProcessorEntry.enable()


def start():
    # We need to instrument the entrypoints on boot because this is the only
    # time the tracer will notify us of entrypoints being registered.
    SpanCodeOriginProcessorEntry.init()

    if config.span.enabled:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorExit

        SpanCodeOriginProcessorExit.enable()

        _start()
    # If dynamic instrumentation is enabled, and code origin for spans is not explicitly disabled,
    # we'll enable entry spans only.
    elif product_manager.is_enabled(DI_PRODUCT_KEY) and config.value_source(CO_ENABLED) == ValueSource.DEFAULT:
        _start()


def restart(join=False):
    pass


def _stop():
    SpanCodeOriginProcessorEntry.disable()


def stop(join=False):
    if config.span.enabled:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorExit

        SpanCodeOriginProcessorEntry.disable()
        SpanCodeOriginProcessorExit.disable()
    elif product_manager.is_enabled(DI_PRODUCT_KEY):
        SpanCodeOriginProcessorEntry.disable()


def at_exit(join=False):
    stop(join=join)


class APMCapabilities(enum.IntFlag):
    APM_TRACING_ENABLE_CODE_ORIGIN = 1 << 40


def apm_tracing_rc(lib_config, _config):
    if (enabled := lib_config.get("code_origin_enabled")) is not None:
        should_start = (config.span.spec.enabled.full_name not in config.source or config.span.enabled) and enabled
        _start() if should_start else _stop()
