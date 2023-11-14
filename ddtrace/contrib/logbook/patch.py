import logbook

import ddtrace
from ddtrace import config

from ...internal.utils import get_argument_value
from ...vendor.wrapt import wrap_function_wrapper as _w
from ..logging.constants import RECORD_ATTR_ENV
from ..logging.constants import RECORD_ATTR_SERVICE
from ..logging.constants import RECORD_ATTR_SPAN_ID
from ..logging.constants import RECORD_ATTR_TRACE_ID
from ..logging.constants import RECORD_ATTR_VALUE_EMPTY
from ..logging.constants import RECORD_ATTR_VALUE_ZERO
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
    span = ddtrace.tracer.current_span()

    trace_id = None
    span_id = None
    if span:
        span_id = span.span_id
        trace_id = span.trace_id
        if config._128_bit_trace_id_enabled and not config._128_bit_trace_id_logging_enabled:
            trace_id = span._trace_id_64bits

    # add ids to logbook event dictionary
    event_dict[RECORD_ATTR_TRACE_ID] = str(trace_id or RECORD_ATTR_VALUE_ZERO)
    event_dict[RECORD_ATTR_SPAN_ID] = str(span_id or RECORD_ATTR_VALUE_ZERO)
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
