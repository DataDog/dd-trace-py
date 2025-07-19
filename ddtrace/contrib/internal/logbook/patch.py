from typing import Dict

import logbook
from wrapt import wrap_function_wrapper as _w

import ddtrace
from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal.utils import get_argument_value


config._add(
    "logbook",
    dict(),
)


def get_version():
    # type: () -> str
    return getattr(logbook, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"logbook": ">=1.0.0"}


def _w_process_record(func, instance, args, kwargs):
    # patch logger to include datadog info before logging
    if config._logs_injection:
        record = get_argument_value(args, kwargs, 0, "record")
        record.extra.update(ddtrace.tracer.get_log_correlation_context())
    return func(*args, **kwargs)


def patch():
    """
    Patch ``logbook`` module for injection of tracer information
    by editing a log record created via ``logbook.base.RecordDispatcher.process_record``
    """
    if getattr(logbook, "_datadog_patch", False):
        return
    logbook._datadog_patch = True

    _w(logbook.base.RecordDispatcher, "process_record", _w_process_record)


def unpatch():
    if getattr(logbook, "_datadog_patch", False):
        logbook._datadog_patch = False

        _u(logbook.base.RecordDispatcher, "process_record")
