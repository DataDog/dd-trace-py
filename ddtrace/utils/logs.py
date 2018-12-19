import logging
from wrapt import wrap_function_wrapper as _w

from ddtrace import correlation
from ddtrace.compat import PY2
from ddtrace.utils.wrappers import unwrap as _u


def _w_makeRecord(func, instance, args, kwargs):
    record = func(*args, **kwargs)

    # add correlation identifiers to LogRecord
    trace_id, span_id = correlation.get_correlation_ids()
    if trace_id:
        record.trace_id = trace_id
        record.span_id = span_id
    else:
        record.trace_id = None
        record.span_id = None

    return record

def _w_format(func, instance, args, kwargs):
    fmt_traced_tmpl = '{} - dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s'

    record = args[0]

    if getattr(record, 'trace_id', False) and getattr(record, 'span_id', False):
        if PY2:
            # only specify traced formatter once per instance
            if not hasattr(instance, '_fmt__dd'):
                setattr(instance, '_fmt__orig', instance._fmt)
                setattr(instance, '_fmt__dd', fmt_traced_tmpl.format(instance._fmt))

            instance._fmt = instance._fmt__dd
            result = func(*args, **kwargs)
            instance._fmt = instance._fmt__orig
        else:
            # only specify traced formatter once per instance
            if not hasattr(instance, '_fmt__dd'):
                setattr(instance, '_fmt__orig', instance._style._fmt)
                setattr(instance, '_fmt__dd', fmt_traced_tmpl.format(instance._style._fmt))

            instance._style._fmt = instance._fmt__dd
            result = func(*args, **kwargs)
            instance._style._fmt = instance._fmt__orig

    else:
        result = func(*args, **kwargs)

    return result


def patch_logging():
    """
    Patch ``logging`` module in the Python Standard Library for injection of
    tracer information by wrapping the base factory method ``Logger.makeRecord``
    """
    if getattr(logging, '_datadog_patch', False):
        return
    setattr(logging, '_datadog_patch', True)

    _w(logging.Logger, 'makeRecord', _w_makeRecord)
    _w(logging.Formatter, 'format', _w_format)

def unpatch_logging():
    if getattr(logging, '_datadog_patch', False):
        setattr(logging, '_datadog_patch', False)

        _u(logging.Logger, 'makeRecord')
