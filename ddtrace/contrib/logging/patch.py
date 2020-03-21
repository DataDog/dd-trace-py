import logging

import ddtrace

from ...constants import ENV_KEY, VERSION_KEY
from ...utils.wrappers import unwrap as _u
from ...vendor.wrapt import wrap_function_wrapper as _w

RECORD_ATTR_TRACE_ID = "dd.trace_id"
RECORD_ATTR_SPAN_ID = "dd.span_id"
RECORD_ATTR_ENV = "dd.env"
RECORD_ATTR_VERSION = "dd.version"
RECORD_ATTR_SERVICE = "dd.service"
RECORD_ATTR_VALUE_ZERO = 0
RECORD_ATTR_VALUE_EMPTY = ""

ddtrace.config._add("logging", dict(tracer=None,))  # by default, override here for custom tracer


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

    # Get the currently active span, if there is one
    span = _get_current_span(tracer=ddtrace.config.logging.tracer)

    # Inject `dd.version`
    # Order of precedence:
    #   - `version` tag on the currently active span
    #   - `config.version` config (`DD_VERSION` env)
    #   - empty string
    version = None
    if span:
        version = span.get_tag(VERSION_KEY)
    version = version or ddtrace.config.version or RECORD_ATTR_VALUE_EMPTY
    setattr(record, RECORD_ATTR_VERSION, version)

    # Inject `dd.env`
    # Order of precedence:
    #   - `env` tag on the currently active span
    #   - `config.env` config (`DD_ENV` env)
    #   - empty string
    env = None
    if span:
        env = span.get_tag(ENV_KEY)
    env = env or ddtrace.config.env or RECORD_ATTR_VALUE_EMPTY
    setattr(record, RECORD_ATTR_ENV, env)

    service = ""
    if span:
        service = span.service or RECORD_ATTR_VALUE_EMPTY
    setattr(record, RECORD_ATTR_SERVICE, service)

    # Inject `dd.trace_id` and `dd.span_id`
    if span:
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
