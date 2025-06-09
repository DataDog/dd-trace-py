from ddtrace.internal.products import manager as product_manager
from ddtrace.settings._core import ValueSource
from ddtrace.settings.code_origin import config


CO_ENABLED = "DD_CODE_ORIGIN_FOR_SPANS_ENABLED"
DI_PRODUCT_KEY = "dynamic-instrumentation"

# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload():
    pass


def start():
    if config.span.enabled:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessor
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

        SpanCodeOriginProcessorEntry.enable()
        SpanCodeOriginProcessor.enable()
    # If dynamic instrumentation is enabled, and code origin for spans is not explicitly disabled,
    # we'll enable entry spans only.
    elif product_manager.is_enabled(DI_PRODUCT_KEY) and config.value_source(CO_ENABLED) == ValueSource.DEFAULT:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

        SpanCodeOriginProcessorEntry.enable()


def restart(join=False):
    pass


def stop(join=False):
    if config.span.enabled:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessor
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

        SpanCodeOriginProcessorEntry.disable()
        SpanCodeOriginProcessor.disable()
    elif product_manager.is_enabled(DI_PRODUCT_KEY):
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

        SpanCodeOriginProcessorEntry.disable()


def at_exit(join=False):
    stop(join=join)
