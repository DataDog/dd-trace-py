import structlog
from wrapt import wrap_function_wrapper as _w

import ddtrace
from ddtrace import config

from ...internal.utils import get_argument_value
from ...internal.utils import set_argument_value
from ..logging.constants import RECORD_ATTR_ENV
from ..logging.constants import RECORD_ATTR_SERVICE
from ..logging.constants import RECORD_ATTR_SPAN_ID
from ..logging.constants import RECORD_ATTR_TRACE_ID
from ..logging.constants import RECORD_ATTR_VALUE_EMPTY
from ..logging.constants import RECORD_ATTR_VALUE_ZERO
from ..logging.constants import RECORD_ATTR_VERSION
from ..trace_utils import unwrap as _u


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


def _w_configure(func, instance, args, kwargs):

    dd_processor = [_tracer_injection]

    # Only inject values if there is some sort of pre-existing processing with a valid renderer.
    # Without this assumption, the logs will not format correctly since it can't accept the injected values
    # This way we do not change their format and mess up any existing logs

    arg_processors = get_argument_value(args, kwargs, 0, "processors", True)
    if arg_processors and len(arg_processors) != 0:
        set_argument_value(args, kwargs, 0, "processors", dd_processor + arg_processors)

    return func(*args, **kwargs)


def patch():
    """
    Patch ``structlog`` module for injection of tracer information
    by appending a processor via the configure block ``structlog.configure``
    """
    if getattr(structlog, "_datadog_patch", False):
        return
    structlog._datadog_patch = True

    _w(structlog, "configure", _w_configure)


def unpatch():
    if getattr(structlog, "_datadog_patch", False):
        structlog._datadog_patch = False

        _u(structlog, "configure")
