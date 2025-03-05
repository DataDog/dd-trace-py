from functools import lru_cache as cached
import hashlib
import io
import logging
import sys
import traceback

import ddtrace
from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace._trace.span import SpanEvent
from ddtrace.internal.utils.cache import callonce
from ddtrace.settings.errortracking import config as errortracking_config


_error_tuple_info = (None, None, None)


@cached(maxsize=8192)
def _get_formatted_traceback(tb_hash):
    """Most of the overhead of automatic reporting is added by traceback formatting
    Cache was added to try to be faster is the same error is reported several times
    """

    exc_type, exc_val, exc_tb = _error_tuple_info
    buff = io.StringIO()
    limit = int(config._span_traceback_max_size)
    traceback.print_exception(exc_type, exc_val, exc_tb, file=buff, limit=limit)
    return buff.getvalue()


def _generate_span_event(exc=None) -> tuple[Exception, Span, SpanEvent] | None:
    """Generate the exception span event"""
    global _error_tuple_info

    span = ddtrace.tracer.current_span()
    if not span:
        return None

    if not exc:
        _, exc, _ = sys.exc_info()
        if not exc:
            return None

    # store the information globally so the cached function can access it
    # passing this information as function arguments would be less efficient
    _error_tuple_info = type(exc), exc, exc.__traceback__  # type: ignore

    # compute a hash of a traceback for caching purpose
    tb_list = traceback.extract_tb(_error_tuple_info[2])
    tb_str = "".join(f"{frame.filename}:{frame.lineno}:{frame.name}" for frame in tb_list)
    tb_hash = hashlib.sha256(tb_str.encode()).hexdigest()
    tb = _get_formatted_traceback(tb_hash)

    return (
        exc,
        span,
        SpanEvent(
            "exception",
            {
                "exception.message": str(exc),
                "exception.type": "%s.%s" % (exc.__class__.__module__, exc.__class__.__name__),
                "exception.stacktrace": tb,
            },
        ),
    )


def _add_span_events(span: Span) -> None:
    """
    If the same error is handled/rethrown multiple times, we want
    to report only one span events. Therefore, we do not add directly
    a span event for every handled exceptions, we store them in the span
    and add them when the span finishes.
    """
    for event in span._exception_events.values():
        span._events.append(event)
    del span._meta["EXCEPTION_CB"]


def _conditionally_pop_span_events(span: Span) -> None:
    """
    This function drops all the span events if no unhandled
    exception occurred.
    """

    if span.error == 1:
        for event in span._exception_events.values():
            span._events.append(event)
    del span._meta["EXCEPTION_CB"]


def _log_span_events(span: Span) -> None:
    """Log the handled exceptions. Will be removed when error track is on"""
    if errortracking_config._internal_logger:
        logger = _get_logger()
        if not logger:
            return
        for error in span._exception_events.keys():
            logger.exception(str(error), exc_info=(type(error), error, error.__traceback__))
    del span._meta["EXCEPTION_LOG_CB"]


def _default_datadog_exc_callback(*args, exc=None):
    generated = _generate_span_event(exc)
    if generated is not None:
        exc, span, span_event = generated
        span._add_exception_event(exc, span_event)
        # Add callbacks to be called on span finish.
        span._add_on_finish_exception_cb(_log_span_events, "EXCEPTION_LOG_CB")
        span._add_on_finish_exception_cb(_add_span_events, "EXCEPTION_CB")


def _unhandled_exc_datadog_exc_callback(*args, exc=None):
    generated = _generate_span_event(exc)
    if generated is not None:
        exc, span, span_event = generated
        span._add_exception_event(exc, span_event)
        # Add callbacks to be called on span finish.
        span._add_on_finish_exception_cb(_log_span_events, "EXCEPTION_LOG_CB")
        span._add_on_finish_exception_cb(_conditionally_pop_span_events, "EXCEPTION_CB")


@callonce
def _get_logger():
    logger_name: str = errortracking_config._internal_logger
    return logging.getLogger(logger_name)
