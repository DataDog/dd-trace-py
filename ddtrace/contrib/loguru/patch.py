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

    event_dd_attributes = {}
    # add ids to loguru event dictionary
    event_dd_attributes[RECORD_ATTR_TRACE_ID] = str(trace_id or RECORD_ATTR_VALUE_ZERO)
    event_dd_attributes[RECORD_ATTR_SPAN_ID] = str(span_id or RECORD_ATTR_VALUE_ZERO)
    # add the env, service, and version configured for the tracer
    event_dd_attributes[RECORD_ATTR_ENV] = config.env or RECORD_ATTR_VALUE_EMPTY
    event_dd_attributes[RECORD_ATTR_SERVICE] = config.service or RECORD_ATTR_VALUE_EMPTY
    event_dd_attributes[RECORD_ATTR_VERSION] = config.version or RECORD_ATTR_VALUE_EMPTY

    event_dict.update(event_dd_attributes)

    return event_dd_attributes


def _w_configure(func, instance, args, kwargs):
    original_patcher = kwargs.get("patcher", None)
    instance._dd_original_patcher = original_patcher
    if not original_patcher:
        # no patcher, we do not need to worry about ddtrace fields being overridden
        return func(*args, **kwargs)

    def _wrapped_patcher(record):
        original_patcher(record)
        record.update(_tracer_injection(record["extra"]))

    kwargs["patcher"] = _wrapped_patcher
    return func(*args, **kwargs)


def patch():
    """
    Patch ``loguru`` module for injection of tracer information
    by appending a patcher before the add function ``loguru.add``
    """
    if getattr(loguru, "_datadog_patch", False):
        return
    loguru._datadog_patch = True
    # Adds ddtrace fields to loguru logger
    loguru.logger.configure(patcher=lambda record: record.update(_tracer_injection(record["extra"])))
    # Ensures that calling loguru.logger.configure(..) does not overwrite ddtrace fields
    _w(loguru.logger, "configure", _w_configure)


def unpatch():
    if getattr(loguru, "_datadog_patch", False):
        loguru._datadog_patch = False

        _u(loguru.logger, "configure")
        if hasattr(loguru.logger, "_dd_original_patcher"):
            loguru.logger.configure(patcher=loguru.logger._dd_original_patcher)
