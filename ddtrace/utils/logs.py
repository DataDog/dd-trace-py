import logging
from ddtrace import correlation
from ddtrace.utils.wrappers import unwrap as _u
from wrapt import wrap_function_wrapper as _w

def _w_makeRecord(func, instance, args, kwargs):
    record = func(*args, **kwargs)

    trace_id, span_id = correlation.get_correlation_ids()
    if trace_id:
        record.trace_id = trace_id
        record.span_id = span_id
    else:
        record.trace_id = None
        record.span_id = None

    return record

def patch_logging():
    if getattr(logging, '_datadog_patch', False):
        return
    setattr(logging, '_datadog_patch', True)

    _w(logging.Logger, 'makeRecord', _w_makeRecord)

def unpatch_logging():
    if getattr(logging, '_datadog_patch', False):
        setattr(logging, '_datadog_patch', False)

        _u(logging.Logger, 'makeRecord')
