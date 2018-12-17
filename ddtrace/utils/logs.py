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

    return record

def patch_log_injection():
    if getattr(logging, '_datadog_patch', False):
        return
    setattr(logging, '_datadog_patch', True)

    _w(logging.Logger, 'makeRecord', _w_makeRecord)

def unpatch_log_injection():
    if getattr(logging, '_datadog_patch', False):
        setattr(logging, '_datadog_patch', False)

        _u(logging.Logger, 'makeRecord')
