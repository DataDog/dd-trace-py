import loguru

from wrapt import wrap_function_wrapper as _w

import ddtrace
from ddtrace import config

from ..trace_utils import unwrap as _u

RECORD_ATTR_TRACE_ID = "dd.trace_id"
RECORD_ATTR_SPAN_ID = "dd.span_id"
RECORD_ATTR_ENV = "dd.env"
RECORD_ATTR_VERSION = "dd.version"
RECORD_ATTR_SERVICE = "dd.service"
RECORD_ATTR_VALUE_ZERO = "0"
RECORD_ATTR_VALUE_EMPTY = ""

config._add(
    "loguru",
    dict(
        tracer=None,
    ),
)  # by default, override here for custom tracer


def get_version():
    # type: () -> str
    return getattr(loguru, "__version__", "")


def tracer_injection(event_dict):
    # get correlation ids from current tracer context
    span = ddtrace.tracer.current_span()

    trace_id = span.trace_id
    if config._128_bit_trace_id_enabled and not config._128_bit_trace_id_logging_enabled:
        trace_id = span._trace_id_64bits

    dd_trace_id, dd_span_id = (trace_id, span.span_id) if span else (None, None)

    # add ids to loguru event dictionary
    event_dict[RECORD_ATTR_TRACE_ID] = str(dd_trace_id or RECORD_ATTR_VALUE_ZERO)
    event_dict[RECORD_ATTR_SPAN_ID] = str(dd_span_id or RECORD_ATTR_VALUE_ZERO)

    # add the env, service, and version configured for the tracer
    event_dict[RECORD_ATTR_ENV] = config.env or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_SERVICE] = config.service or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_VERSION] = config.version or RECORD_ATTR_VALUE_EMPTY

    return event_dict


def _w_add(func, instance, args, kwargs):
    # patch logger to include datadog info before logging
    #import pdb; pdb.set_trace()
    extra = {"context": "foo"}
    instance.configure(patcher=lambda record: record["extra"].update(tracer_injection(record)))
    #instance.configure(extra=extra)

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
