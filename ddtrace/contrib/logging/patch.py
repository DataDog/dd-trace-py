import logging

import ddtrace

from ...utils.wrappers import unwrap as _u
from ...vendor.wrapt import wrap_function_wrapper as _w


RECORD_ATTR_TRACE_ID = "dd.trace_id"
RECORD_ATTR_SPAN_ID = "dd.span_id"
RECORD_ATTR_ENV = "dd.env"
RECORD_ATTR_VERSION = "dd.version"
RECORD_ATTR_SERVICE = "dd.service"
RECORD_ATTR_VALUE_ZERO = "0"
RECORD_ATTR_VALUE_EMPTY = ""

ddtrace.config._add(
    "logging",
    dict(
        tracer=None,
    ),
)  # by default, override here for custom tracer


def _get_current_span(tracer=None):
    """Helper to get the currently active span"""
    if not tracer:
        tracer = ddtrace.tracer

    if not tracer.enabled:
        return None

    return tracer.current_span()


def _w_makeRecord(func, instance, args, kwargs):
    # Get the LogRecord instance for this log
    record = func(*args, **kwargs)

    setattr(record, RECORD_ATTR_VERSION, ddtrace.config.version or "")
    setattr(record, RECORD_ATTR_ENV, ddtrace.config.env or "")
    setattr(record, RECORD_ATTR_SERVICE, ddtrace.config.service or "")

    # logs from internal logger may explicitly pass the current span to
    # avoid deadlocks in getting the current span while already in locked code.
    span_from_log = getattr(record, ddtrace.constants.LOG_SPAN_KEY, None)
    if isinstance(span_from_log, ddtrace.Span):
        span = span_from_log
    else:
        span = _get_current_span(tracer=ddtrace.config.logging.tracer)

    if span:
        setattr(record, RECORD_ATTR_TRACE_ID, str(span.trace_id))
        setattr(record, RECORD_ATTR_SPAN_ID, str(span.span_id))
    else:
        setattr(record, RECORD_ATTR_TRACE_ID, RECORD_ATTR_VALUE_ZERO)
        setattr(record, RECORD_ATTR_SPAN_ID, RECORD_ATTR_VALUE_ZERO)

    return record


def patch():
    """
    Patch ``logging`` module in the Python Standard Library for injection of
    tracer information by wrapping the base factory method ``Logger.makeRecord``
    """
    if getattr(logging, "_datadog_patch", False):
        return
    setattr(logging, "_datadog_patch", True)

    _w(logging.Logger, "makeRecord", _w_makeRecord)


def unpatch():
    if getattr(logging, "_datadog_patch", False):
        setattr(logging, "_datadog_patch", False)

        _u(logging.Logger, "makeRecord")
