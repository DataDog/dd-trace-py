import enum
from functools import partial
from types import FunctionType
from types import MethodType
import typing as t

import ddtrace.internal.core as core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.products import manager as product_manager
from ddtrace.internal.settings._core import ValueSource
from ddtrace.internal.settings.code_origin import config


log = get_logger(__name__)

CO_ENABLED = "DD_CODE_ORIGIN_FOR_SPANS_ENABLED"
DI_PRODUCT_KEY = "dynamic-instrumentation"

# TODO[gab]: Uncomment this when the feature is ready
# requires = ["tracer"]


def post_preload():
    pass


def _start():
    from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

    SpanCodeOriginProcessorEntry.enable()


def start():
    # We need to instrument the entrypoints on boot because this is the only
    # time the tracer will notify us of entrypoints being registered.
    @partial(core.on, "service_entrypoint.patch")
    def _(f: t.Union[FunctionType, MethodType]) -> None:
        from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

        SpanCodeOriginProcessorEntry.instrument_view(f)

    log.debug("Registered entrypoint patching hook for code origin for spans")

    # If dynamic instrumentation is enabled, and code origin for spans is not explicitly disabled,
    # we'll enable code origin for spans.
    di_enabled = product_manager.is_enabled(DI_PRODUCT_KEY) and config.value_source(CO_ENABLED) == ValueSource.DEFAULT
    if config.span.enabled or di_enabled:
        _start()


def restart(join=False):
    pass


def _stop():
    from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry

    SpanCodeOriginProcessorEntry.disable()


def stop(join=False):
    di_enabled = product_manager.is_enabled(DI_PRODUCT_KEY) and config.value_source(CO_ENABLED) == ValueSource.DEFAULT
    if config.span.enabled or di_enabled:
        _stop()


def at_exit(join=False):
    stop(join=join)


class APMCapabilities(enum.IntFlag):
    APM_TRACING_ENABLE_CODE_ORIGIN = 1 << 40


def apm_tracing_rc(lib_config, _config):
    if (enabled := lib_config.get("code_origin_enabled")) is not None:
        should_start = (config.span.spec.enabled.full_name not in config.source or config.span.enabled) and enabled
        _start() if should_start else _stop()
