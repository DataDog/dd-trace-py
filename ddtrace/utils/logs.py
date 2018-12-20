"""
The patching of standard library logging to inject tracing information

Example::
    import logging
    from ddtrace import tracer
    from ddtrace.utils.logs import patch_logging

    patch_logging()
    logging.basicConfig(format='%(asctime)-15s %(message)s - dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s')
    log = logging.getLogger()
    log.level = logging.INFO


    @tracer.wrap()
    def foo():
        log.info('Hello!')

    foo()
"""

import logging
from wrapt import wrap_function_wrapper as _w

from ddtrace import correlation
from ddtrace.utils.wrappers import unwrap as _u


def _w_makeRecord(func, instance, args, kwargs):
    record = func(*args, **kwargs)

    # add correlation identifiers to LogRecord
    trace_id, span_id = correlation.get_correlation_ids()
    if trace_id:
        record.trace_id = trace_id
        record.span_id = span_id
    else:
        record.trace_id = 0
        record.span_id = 0

    return record


def patch_logging():
    """
    Patch ``logging`` module in the Python Standard Library for injection of
    tracer information by wrapping the base factory method ``Logger.makeRecord``
    """
    if getattr(logging, '_datadog_patch', False):
        return
    setattr(logging, '_datadog_patch', True)

    _w(logging.Logger, 'makeRecord', _w_makeRecord)


def unpatch_logging():
    if getattr(logging, '_datadog_patch', False):
        setattr(logging, '_datadog_patch', False)

        _u(logging.Logger, 'makeRecord')
