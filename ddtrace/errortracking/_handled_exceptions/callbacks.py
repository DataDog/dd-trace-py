from functools import lru_cache as cached
import hashlib
import io
import sys
import traceback

from ddtrace import config
from ddtrace import tracer
from ddtrace._trace.span import Span
from ddtrace._trace.span import SpanEvent
from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector


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


def _generate_span_event(span: Span, exc=None) -> tuple[Exception, Span, SpanEvent] | None:
    """Generate the exception span event"""
    global _error_tuple_info

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


def _default_bytecode_exc_callback(*args, exc=None):
    span = tracer.current_span()
    if span:
        _default_errortracking_exc_callback(span=span, exc=exc)


def _default_errortracking_exc_callback(*args, span: Span, exc=None):
    generated = _generate_span_event(span, exc)
    if generated is not None:
        exc, span, span_event = generated
        HandledExceptionCollector.capture_exception_event(span, exc, span_event)
