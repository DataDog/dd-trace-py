import logging
from ddtrace import correlation
from wrapt import wrap_function_wrapper as _w

def _w_makeRecord(func, instance, args, kwargs):
    record = func(*args, **kwargs)

    trace_id, span_id = correlation.get_correlation_ids()
    print trace_id, span_id
    if trace_id:
        record.trace_id = trace_id
        record.span_id = span_id

    return record

def patch_log_injection():
    _w(logging.Logger, 'makeRecord', _w_makeRecord)
