import importlib
import io
import sys
import traceback

import ddtrace
from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace._trace.span import SpanEvent
from ddtrace.internal.packages import filename_to_package
from ddtrace.settings.error_reporting import _er_config


_internal_debug_logger = None


def _generate_span_event(exc=None) -> tuple[Span, SpanEvent] | None:
    span = ddtrace.tracer.current_span()
    if not span:
        return None

    if not exc:
        _, exc, _ = sys.exc_info()
        if not exc:
            return None

    limit = int(config._span_traceback_max_size)

    # The most complete
    # tb = "Traceback (most recent call last):\n"
    # tb += "".join(traceback.format_stack(limit=limit + 1)[:-2])

    # Best compromise
    # tb = "Traceback (most recent call last):\n"
    # tb += "".join(traceback.format_tb(exc.__traceback__, limit=limit))
    # tb += f"{exc.__class__.__name__}: {exc}"

    # Test 3 -> unhandled error way
    buff = io.StringIO()
    exc_type, exc_val, exc_tb = type(exc), exc, exc.__traceback__
    traceback.print_exception(exc_type, exc_val, exc_tb, file=buff, limit=limit)
    tb = buff.getvalue()

    # Test -> potential most performant
    # tb = "Traceback (most recent call last):\n"
    # for tb_frame in traceback.extract_tb(exc.__traceback__, limit=limit):
    #     tb += f'\tFile "{tb_frame.filename}", line {tb_frame.lineno}, in {tb_frame.name}\n'
    #     if tb_frame.line:
    #         for line in tb_frame._dedented_lines.split("\n")[:-1]:
    #             tb += f'\t\t{line}\n'
    # tb += f"{exc.__class__.__name__}: {exc}"

    # know which package it is
    source = "unknown"
    if exc.__traceback__ is not None:
        source = "user"
        filename = exc.__traceback__.tb_frame.f_code.co_filename
        third_party_package = filename_to_package(filename)
        if third_party_package is not None:
            source = third_party_package.name

    return span, SpanEvent(
        "handled exception",
        {
            "exception.message": str(exc),
            "exception.type": "%s.%s" % (exc.__class__.__module__, exc.__class__.__name__),
            "exception.stacktrace": tb,
            "exception.source": source,
        },
    )


def _conditionally_pop_span_events(span: Span) -> None:
    if span.error == 1:
        span._exception_events.popitem()
        for event in span._exception_events.values():
            span._events.append(event)
    del span._meta["EXCEPTION_CB"]


def _add_span_events(span: Span) -> None:
    if span.error == 1:
        span._exception_events.popitem()
    for event in span._exception_events.values():
        span._events.append(event)
    del span._meta["EXCEPTION_CB"]


def _default_datadog_exc_callback(*args, exc=None):
    generated = _generate_span_event(exc)
    if generated is not None:
        span, span_event = generated
        span._add_exception_event(exc.__hash__(), span_event)
        span._add_on_finish_exception_cb(_add_span_events)

    if _er_config._internal_logger:
        logger = _get_logger()
        if not logger:
            return
        logger.exception("Handled exception")


def _unhandled_exc_datadog_exc_callback(*args, exc=None):
    generated = _generate_span_event(exc)
    if generated is not None:
        span, span_event = generated
        span._add_exception_event(exc.__hash__(), span_event)
        span._add_on_finish_exception_cb(_conditionally_pop_span_events)

    if _er_config._internal_logger:
        logger = _get_logger()
        if not logger:
            return
        logger.exception("Handled exception")


def _get_logger():
    if not _er_config._internal_logger:
        return

    _debug_logger_path: str = _er_config._internal_logger
    logger_path, logger_name = _debug_logger_path.rsplit(".", 1)
    module = importlib.import_module(logger_path)
    return getattr(module, logger_name)
