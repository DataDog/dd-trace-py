import structlog

import ddtrace
from ddtrace import config

from ..logging.constants import RECORD_ATTR_ENV
from ..logging.constants import RECORD_ATTR_SERVICE
from ..logging.constants import RECORD_ATTR_SPAN_ID
from ..logging.constants import RECORD_ATTR_TRACE_ID
from ..logging.constants import RECORD_ATTR_VALUE_EMPTY
from ..logging.constants import RECORD_ATTR_VALUE_ZERO
from ..logging.constants import RECORD_ATTR_VERSION
from ..trace_utils import unwrap as _u
from ..trace_utils import wrap as _w


config._add(
    "structlog",
    dict(),
)


def get_version():
    # type: () -> str
    return getattr(structlog, "__version__", "")


def _tracer_injection(_, __, event_dict):
    span = ddtrace.tracer.current_span()

    trace_id = None
    span_id = None
    if span:
        span_id = span.span_id
        trace_id = span.trace_id
        if config._128_bit_trace_id_enabled and not config._128_bit_trace_id_logging_enabled:
            trace_id = span._trace_id_64bits

    # add ids to structlog event dictionary
    event_dict[RECORD_ATTR_TRACE_ID] = str(trace_id or RECORD_ATTR_VALUE_ZERO)
    event_dict[RECORD_ATTR_SPAN_ID] = str(span_id or RECORD_ATTR_VALUE_ZERO)
    # add the env, service, and version configured for the tracer
    event_dict[RECORD_ATTR_ENV] = config.env or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_SERVICE] = config.service or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_VERSION] = config.version or RECORD_ATTR_VALUE_EMPTY

    return event_dict


def _w_get_logger(func, instance, args, kwargs):
    """
    Append the tracer injection processor to the ``default_processors`` list used by the logger
    The ``default_processors`` list has built in defaults which protects against a user configured ``None`` value.
    The argument to configure ``default_processors`` accepts an iterable type:
        - List: default use case which has been accounted for
        - Tuple: patched via list conversion
        - Set: ignored because structlog processors care about order notably the last value to be a Renderer
        - Dict: because keys are ignored, this essentially becomes a List
    """

    dd_processor = [_tracer_injection]
    structlog._config._CONFIG.default_processors = dd_processor + list(structlog._config._CONFIG.default_processors)
    return func(*args, **kwargs)


def patch():
    """
    Patch ``structlog`` module for injection of tracer information
    by appending a processor before creating a logger via ``structlog.get_logger``
    """
    if getattr(structlog, "_datadog_patch", False):
        return
    structlog._datadog_patch = True

    if hasattr(structlog, "get_logger"):
        _w(structlog, "get_logger", _w_get_logger)

    # getLogger is an alias for get_logger
    if hasattr(structlog, "getLogger"):
        _w(structlog, "getLogger", _w_get_logger)


def unpatch():
    if getattr(structlog, "_datadog_patch", False):
        structlog._datadog_patch = False

        if hasattr(structlog, "get_logger"):
            _u(structlog, "get_logger")
        if hasattr(structlog, "getLogger"):
            _u(structlog, "getLogger")
