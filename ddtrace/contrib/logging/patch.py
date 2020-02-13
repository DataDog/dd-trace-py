import logging

import ddtrace

from ...helpers import get_correlation_ids
from ...utils.wrappers import unwrap as _u
from ...vendor.wrapt import wrap_function_wrapper as _w

RECORD_ATTR_VERSION = "dd.version"
RECORD_ATTR_TRACE_ID = "dd.trace_id"
RECORD_ATTR_SPAN_ID = "dd.span_id"
RECORD_ATTR_VALUE_ZERO = 0
RECORD_ATTR_VALUE_EMPTY = ""

ddtrace.config._add("logging", dict(tracer=None,))  # by default, override here for custom tracer


def _inject_or_default(record, key, value, default=RECORD_ATTR_VALUE_EMPTY):
    if not value:
        value = default
    setattr(record, key, value)


def _w_makeRecord(func, instance, args, kwargs):
    record = func(*args, **kwargs)

    # DEV: We must *always* inject these variables into the record, even if we don't
    #      have an active span, if someone hard codes their format string to add these
    #      then they must be there

    tracer = ddtrace.config.logginng.tracer or ddtrace.tracer
    span = tracer.current_span()

    # Add the application version to LogRecord
    _inject_or_default(record, RECORD_ATTR_VERSION, ddtrace.config.version)

    # TODO: Inject DD_ENV and DD_SERVICE

    # add correlation identifiers to LogRecord
    if span.trace_id and span.span_id:
        setattr(record, RECORD_ATTR_TRACE_ID, span.trace_id)
        setattr(record, RECORD_ATTR_SPAN_ID, span.span_id)
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
