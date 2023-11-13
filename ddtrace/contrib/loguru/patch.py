import loguru

import ddtrace
from ddtrace import config

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
    "loguru",
    dict(),
)


def get_version():
    # type: () -> str
    return getattr(loguru, "__version__", "")


def _tracer_injection(event_dict):
    span = ddtrace.tracer.current_span()

    trace_id = None
    span_id = None
    if span:
        span_id = span.span_id
        trace_id = span.trace_id
        if config._128_bit_trace_id_enabled and not config._128_bit_trace_id_logging_enabled:
            trace_id = span._trace_id_64bits

    # add ids to loguru event dictionary
    event_dict[RECORD_ATTR_TRACE_ID] = str(trace_id or RECORD_ATTR_VALUE_ZERO)
    event_dict[RECORD_ATTR_SPAN_ID] = str(span_id or RECORD_ATTR_VALUE_ZERO)
    # add the env, service, and version configured for the tracer
    event_dict[RECORD_ATTR_ENV] = config.env or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_SERVICE] = config.service or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_VERSION] = config.version or RECORD_ATTR_VALUE_EMPTY

    return event_dict


def _w_add(func, instance, args, kwargs):
    # patch logger to include datadog info before logging
    instance.configure(patcher=lambda record: record.update(_tracer_injection(record["extra"])))
    return func(*args, **kwargs)


def patch():
    """
    Patch ``loguru`` module for injection of tracer information
    by appending a patcher before the add function ``loguru.add``
    """
    if getattr(loguru, "_datadog_patch", False):
        return
    loguru._datadog_patch = True

    _w(loguru.logger, "add", _w_add)


def unpatch():
    if getattr(loguru, "_datadog_patch", False):
        loguru._datadog_patch = False

        _u(loguru.logger, "add")
