import logging

import attr

import ddtrace

from ...internal.utils import get_argument_value
from ...vendor.wrapt import wrap_function_wrapper as _w
from ..trace_utils import unwrap as _u


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


@attr.s(slots=True)
class DDLogRecord(object):
    trace_id = attr.ib(type=int)
    span_id = attr.ib(type=int)
    service = attr.ib(type=str)
    version = attr.ib(type=str)
    env = attr.ib(type=str)


def _get_current_span(tracer=None):
    """Helper to get the currently active span"""
    if not tracer:
        tracer = ddtrace.tracer

    # We might be calling this during library initialization, in which case `ddtrace.tracer` might
    # be the `tracer` module and not the global tracer instance.
    if not getattr(tracer, "enabled", False):
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


def _w_StrFormatStyle_format(func, instance, args, kwargs):
    # The format string "dd.service={dd.service}" expects
    # the record to have a "dd" property which is an object that
    # has a "service" property
    # PercentStyle, and StringTemplateStyle both look for
    # a "dd.service" property on the record
    record = get_argument_value(args, kwargs, 0, "record")

    record.dd = DDLogRecord(
        trace_id=getattr(record, RECORD_ATTR_TRACE_ID, RECORD_ATTR_VALUE_ZERO),
        span_id=getattr(record, RECORD_ATTR_SPAN_ID, RECORD_ATTR_VALUE_ZERO),
        service=getattr(record, RECORD_ATTR_SERVICE, ""),
        version=getattr(record, RECORD_ATTR_VERSION, ""),
        env=getattr(record, RECORD_ATTR_ENV, ""),
    )

    try:
        return func(*args, **kwargs)
    finally:
        # We need to remove this extra attribute so it does not pollute other formatters
        # For example: if we format with StrFormatStyle and then  a JSON logger
        # then the JSON logger will have `dd.{service,version,env,trace_id,span_id}` as
        # well as the `record.dd` `DDLogRecord` instance
        del record.dd


def patch():
    """
    Patch ``logging`` module in the Python Standard Library for injection of
    tracer information by wrapping the base factory method ``Logger.makeRecord``
    """
    if getattr(logging, "_datadog_patch", False):
        return
    setattr(logging, "_datadog_patch", True)

    _w(logging.Logger, "makeRecord", _w_makeRecord)
    if hasattr(logging, "StrFormatStyle"):
        if hasattr(logging.StrFormatStyle, "_format"):
            _w(logging.StrFormatStyle, "_format", _w_StrFormatStyle_format)
        else:
            _w(logging.StrFormatStyle, "format", _w_StrFormatStyle_format)


def unpatch():
    if getattr(logging, "_datadog_patch", False):
        setattr(logging, "_datadog_patch", False)

        _u(logging.Logger, "makeRecord")
        if hasattr(logging, "StrFormatStyle"):
            if hasattr(logging.StrFormatStyle, "_format"):
                _u(logging.StrFormatStyle, "_format")
            else:
                _u(logging.StrFormatStyle, "format")
