from ddtrace.settings._core import ValueSource
from ddtrace.settings.code_origin import config
from ddtrace.settings.dynamic_instrumentation import config as di_config


# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload():
    pass


def start():
    if config.span.enabled:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessor

        SpanCodeOriginProcessor.enable()
    # If dynamic instrumentation is enabled, and code origin for spans is not explicitly disabled,
    # we'll enable entry spans only.
    elif di_config.enabled and config.value_source("DD_CODE_ORIGIN_FOR_SPANS_ENABLED") == ValueSource.DEFAULT:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

        SpanCodeOriginProcessorEntry.enable()


def restart(join=False):
    pass


def stop(join=False):
    if config.span.enabled:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessor

        SpanCodeOriginProcessor.disable()
    elif di_config.enabled:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

        SpanCodeOriginProcessorEntry.disable()


def at_exit(join=False):
    stop(join=join)
