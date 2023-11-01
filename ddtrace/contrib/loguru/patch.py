import json

import loguru

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

    # add ids to structlog event dictionary
    event_dict[RECORD_ATTR_TRACE_ID] = str(trace_id or RECORD_ATTR_VALUE_ZERO)
    event_dict[RECORD_ATTR_SPAN_ID] = str(span_id or RECORD_ATTR_VALUE_ZERO)
    # add the env, service, and version configured for the tracer
    event_dict[RECORD_ATTR_ENV] = config.env or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_SERVICE] = config.service or RECORD_ATTR_VALUE_EMPTY
    event_dict[RECORD_ATTR_VERSION] = config.version or RECORD_ATTR_VALUE_EMPTY

    return event_dict


def _w_add(func, instance, args, kwargs):
    # patch logger to include datadog info before logging
    instance.configure(patcher=lambda record: record.update(_tracer_injection(record)))
    return func(*args, **kwargs)


def _w_serialize(func, instance, args, kwargs):
    # recreate internal `_serialize_record` function by appending trace values to serialized and returning JSON
    # does not return wrapped function because log object is hard-coded within the function and thus cannot be edited
    text = get_argument_value(args, kwargs, 0, "text")
    record = get_argument_value(args, kwargs, 1, "record")

    exception = record["exception"]

    if exception is not None:
        exception = {
            "type": None if exception.type is None else exception.type.__name__,
            "value": exception.value,
            "traceback": bool(exception.traceback),
        }

    serializable = {
        "text": text,
        "record": {
            "elapsed": {
                "repr": record["elapsed"],
                "seconds": record["elapsed"].total_seconds(),
            },
            "exception": exception,
            "extra": record["extra"],
            "file": {"name": record["file"].name, "path": record["file"].path},
            "function": record["function"],
            "level": {
                "icon": record["level"].icon,
                "name": record["level"].name,
                "no": record["level"].no,
            },
            "line": record["line"],
            "message": record["message"],
            "module": record["module"],
            "name": record["name"],
            "process": {"id": record["process"].id, "name": record["process"].name},
            "thread": {"id": record["thread"].id, "name": record["thread"].name},
            "time": {"repr": record["time"], "timestamp": record["time"].timestamp()},
        },
        "dd.trace_id": record["dd.trace_id"] if "dd.trace_id" in record else "",
        "dd.span_id": record["dd.span_id"] if "dd.span_id" in record else "",
        "dd.env": record["dd.env"] if "dd.env" in record else "",
        "dd.version": record["dd.version"] if "dd.version" in record else "",
        "dd.service": record["dd.service"] if "dd.service" in record else "",
    }

    return json.dumps(serializable, default=str, ensure_ascii=False) + "\n"


def patch():
    """
    Patch ``loguru`` module for injection of tracer information
    by appending a patcher before the add function ``loguru.add``

    Also overwrites the built-in default JSON output method to
    include injected trace values
    """
    if getattr(loguru, "_datadog_patch", False):
        return
    loguru._datadog_patch = True

    _w(loguru.logger, "add", _w_add)
    _w(loguru._handler.Handler, "_serialize_record", _w_serialize)


def unpatch():
    if getattr(loguru, "_datadog_patch", False):
        loguru._datadog_patch = False

        _u(loguru.logger, "add")
        _u(loguru._handler.Handler, "_serialize_record")
