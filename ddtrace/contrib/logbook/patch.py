import logbook

from ddtrace import config

from ...internal.utils import get_argument_value
from ...vendor.wrapt import wrap_function_wrapper as _w
from ..logging.constants import RECORD_ATTR_ENV
from ..logging.constants import RECORD_ATTR_SERVICE
from ..logging.constants import RECORD_ATTR_SPAN_ID
from ..logging.constants import RECORD_ATTR_TRACE_ID
from ..logging.constants import RECORD_ATTR_VALUE_EMPTY
from ..logging.constants import RECORD_ATTR_VERSION
from ..trace_utils import unwrap as _u


config._add(
    "logbook",
    dict(),
)


def get_version():
    # type: () -> str
    return getattr(logbook, "__version__", "")


def _tracer_injection(event_dict):
    trace_details = config.logging.tracer.get_log_correlation_context()

    # add ids to logbook event dictionary
    event_dict[RECORD_ATTR_TRACE_ID] = trace_details["trace_id"]
    event_dict[RECORD_ATTR_SPAN_ID] = trace_details["span_id"]
    # add the env, service, and version configured for the tracer
    event_dict[RECORD_ATTR_ENV] = config.env or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_SERVICE] = config.service or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_VERSION] = config.version or RECORD_ATTR_VALUE_EMPTY

    return event_dict


def _w_process_record(func, instance, args, kwargs):
    # patch logger to include datadog info before logging
    record = get_argument_value(args, kwargs, 0, "record")
    _tracer_injection(record.extra)
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
