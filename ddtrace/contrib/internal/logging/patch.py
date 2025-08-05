import logging
from typing import Dict

from wrapt import wrap_function_wrapper as _w

import ddtrace
from ddtrace import config
from ddtrace._logger import set_log_formatting
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal.constants import LOG_ATTR_ENV
from ddtrace.internal.constants import LOG_ATTR_SERVICE
from ddtrace.internal.constants import LOG_ATTR_SPAN_ID
from ddtrace.internal.constants import LOG_ATTR_TRACE_ID
from ddtrace.internal.constants import LOG_ATTR_VALUE_EMPTY
from ddtrace.internal.constants import LOG_ATTR_VALUE_ZERO
from ddtrace.internal.constants import LOG_ATTR_VERSION
from ddtrace.internal.utils import get_argument_value


config._add(
    "logging",
    dict(
        tracer=None,
    ),
)


def get_version():
    # type: () -> str
    return getattr(logging, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"logging": "*"}


class DDLogRecord:
    trace_id: int
    span_id: int
    service: str
    version: str
    env: str
    __slots__ = ("trace_id", "span_id", "service", "version", "env")

    def __init__(self, trace_id: int, span_id: int, service: str, version: str, env: str):
        self.trace_id = trace_id
        self.span_id = span_id
        self.service = service
        self.version = version
        self.env = env


def _w_makeRecord(func, instance, args, kwargs):
    # Get the LogRecord instance for this log
    record = func(*args, **kwargs)
    if config._logs_injection:
        record.__dict__.update(ddtrace.tracer.get_log_correlation_context())
    return record


def _w_StrFormatStyle_format(func, instance, args, kwargs):
    if not config._logs_injection:
        return func(*args, **kwargs)
    # The format string "dd.service={dd.service}" expects
    # the record to have a "dd" property which is an object that
    # has a "service" property
    # PercentStyle, and StringTemplateStyle both look for
    # a "dd.service" property on the record
    record = get_argument_value(args, kwargs, 0, "record")
    # TODO(munir): The format string does not need to have a period in the property name.
    # We can use "dd.service={dd_service}" instead and still produce the same log message.
    # This is a breaking change, so we will not do it in this PR.
    record.dd = DDLogRecord(
        trace_id=getattr(record, LOG_ATTR_TRACE_ID, LOG_ATTR_VALUE_ZERO),
        span_id=getattr(record, LOG_ATTR_SPAN_ID, LOG_ATTR_VALUE_ZERO),
        service=getattr(record, LOG_ATTR_SERVICE, LOG_ATTR_VALUE_EMPTY),
        version=getattr(record, LOG_ATTR_VERSION, LOG_ATTR_VALUE_EMPTY),
        env=getattr(record, LOG_ATTR_ENV, LOG_ATTR_VALUE_EMPTY),
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
    logging._datadog_patch = True

    _w(logging.Logger, "makeRecord", _w_makeRecord)
    _w(logging.StrFormatStyle, "_format", _w_StrFormatStyle_format)

    if config._logs_injection:
        # Only set the formatter is DD_LOGS_INJECTION is set to True. We do not want to modify
        # unstructured logs if a user has not enabled logs injection.
        # Also, the Datadog log format must be set after the logging module has been patched,
        # otherwise the formatter will raise an exception.
        set_log_formatting()


def unpatch():
    if getattr(logging, "_datadog_patch", False):
        logging._datadog_patch = False

        _u(logging.Logger, "makeRecord")
        _u(logging.StrFormatStyle, "_format")
