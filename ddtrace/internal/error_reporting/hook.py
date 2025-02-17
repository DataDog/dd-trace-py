from functools import lru_cache as cached
import hashlib
import importlib
import io
import sys
import traceback

import ddtrace
from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace._trace.span import SpanEvent
from ddtrace.settings.error_reporting import config as error_reporting_config


_internal_debug_logger = None
_error_tuple_info = (None, None, None)


@cached(maxsize=4096)
def _get_formatted_traceback(tb_hash):
    exc_type, exc_val, exc_tb = _error_tuple_info
    buff = io.StringIO()
    limit = int(config._span_traceback_max_size)
    traceback.print_exception(exc_type, exc_val, exc_tb, file=buff, limit=limit)
    return buff.getvalue()


def _generate_span_event(exc=None) -> tuple[Exception, Span, SpanEvent] | None:
    global _error_tuple_info

    span = ddtrace.tracer.current_span()
    if not span:
        return None

    if not exc:
        _, exc, _ = sys.exc_info()
        if not exc:
            return None

    _error_tuple_info = type(exc), exc, exc.__traceback__  # type: ignore

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


"""
On python >= 3,12, we are using sys.monitoring
it will automatically records multiple times an error if
it raised during tracing. Therefore we need additional
logic to remove it
"""
if sys.version_info >= (3, 12):

    def _add_span_events(span: Span) -> None:
        if span.error == 1:
            span._exception_events.popitem()
        for event in span._exception_events.values():
            span._events.append(event)
        del span._meta["EXCEPTION_CB"]

    def _conditionally_pop_span_events(span: Span) -> None:
        if span.error == 1:
            span._exception_events.popitem()
            for event in span._exception_events.values():
                span._events.append(event)
        del span._meta["EXCEPTION_CB"]

else:

    def _add_span_events(span: Span) -> None:
        for event in span._exception_events.values():
            span._events.append(event)
            print(event)
        del span._meta["EXCEPTION_CB"]

    def _conditionally_pop_span_events(span: Span) -> None:
        if span.error == 1:
            for event in span._exception_events.values():
                span._events.append(event)
        del span._meta["EXCEPTION_CB"]


def _add_exception_event(exc, span: Span, span_event: SpanEvent):
    span._add_exception_event(exc, span_event)


def _default_datadog_exc_callback(*args, exc=None):
    generated = _generate_span_event(exc)
    if generated is not None:
        exc, span, span_event = generated
        _add_exception_event(exc, span, span_event)
        span._add_on_finish_exception_cb(_add_span_events)

    if error_reporting_config._internal_logger:
        logger = _get_logger()
        if not logger:
            return
        logger.exception("Handled exception")


def _unhandled_exc_datadog_exc_callback(*args, exc=None):
    generated = _generate_span_event(exc)
    if generated is not None:
        exc, span, span_event = generated
        _add_exception_event(exc, span, span_event)
        span._add_on_finish_exception_cb(_conditionally_pop_span_events)

    if error_reporting_config._internal_logger:
        logger = _get_logger()
        if not logger:
            return
        logger.exception("Handled exception")


def _get_logger():
    if not error_reporting_config._internal_logger:
        return

    _debug_logger_path: str = error_reporting_config._internal_logger
    logger_path, logger_name = _debug_logger_path.rsplit(".", 1)
    module = importlib.import_module(logger_path)
    return getattr(module, logger_name)
