import logging
import sys
import typing as t

from ddtrace._trace.span import Span
from ddtrace._trace.span import SpanEvent
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import callonce
from ddtrace.settings.errortracking import config


log = get_logger(__name__)

if config._report_after_unhandled is False:

    def _add_span_events(span: Span) -> None:
        """
        If the same error is handled/rethrown multiple times, we want
        to report only one span events. Therefore, we do not add directly
        a span event for every handled exceptions, we store them in the span
        and add them when the span finishes.
        """
        span_exc_events = HandledExceptionCollector.get_exception_events(span.span_id).values()
        for event in span_exc_events:
            span._events.append(event)

else:

    def _add_span_events(span: Span) -> None:
        """
        This function drops all the span events if no unhandled
        exception occurred.
        """
        span_exc_events = HandledExceptionCollector.get_exception_events(span.span_id).values()
        if span.error == 1:
            for event in span_exc_events:
                span._events.append(event)


def _log_span_events(span: Span) -> None:
    """Log the handled exceptions. Will be removed when error track is on"""
    if config._internal_logger:
        logger = _get_logger()
        if not logger:
            return
        span_exceptions = HandledExceptionCollector.get_exception_events(span.span_id).keys()
        for error in span_exceptions:
            logger.exception(str(error), exc_info=(type(error), error, error.__traceback__))


@callonce
def _get_logger():
    logger_name: str = config._internal_logger
    return logging.getLogger(logger_name)


def init_handled_exceptions_reporting():
    if config.enabled is False:
        return

    if sys.version_info >= (3, 12):
        from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _install_sys_monitoring_reporting

        """
        Starting from python3.12, handled exceptions reporting is based on sys.monitoring. This is
        safer than bytecode injection as it can not alter the behaviour of a program. However,
        sys.monitoring reports every handled exceptions including python internal ones. Therefore,
        we need to add a filtering step which can be time efficient.
        """
        _install_sys_monitoring_reporting()
    elif sys.version_info >= (3, 10):
        from ddtrace.errortracking._handled_exceptions.bytecode_reporting import _install_bytecode_injection_reporting

        """
        For python3.10 and python3.11, handled exceptions reporting is based on bytecode injection.
        This is efficient, as we will instrument only the targeted code. However, it is considered
        unsafe as it could alter the program behavior.Therefore, it will drop for sys.monitoring in
        3.12 (before handled exception events are not supported)
        """
        _install_bytecode_injection_reporting()
    else:
        return


class HandledExceptionCollector:
    _instance: t.Optional["HandledExceptionCollector"] = None

    _span_exception_events: t.Dict[int, t.Dict[Exception, SpanEvent]] = {}

    @classmethod
    def capture_exception_event(cls, span: Span, exc: Exception, event: SpanEvent):
        span_id = span.span_id
        if span_id not in cls._span_exception_events:
            cls._span_exception_events[span_id] = {}
            # Add callbacks to be called on span finish
            span._add_on_finish_exception_callback(_log_span_events)
            span._add_on_finish_exception_callback(_add_span_events)
        cls._span_exception_events[span_id][exc] = event

    @classmethod
    def get_exception_events(cls, span_id: int):
        return cls._span_exception_events[span_id]

    @classmethod
    def enable(cls) -> None:
        if cls._instance is not None:
            log.debug("HandledExceptionCollector already enabled")

        log.debug("Enabling HandledExceptionCollector")
        try:
            init_handled_exceptions_reporting()
            cls._instance = cls()
        except Exception:
            log.error("Failed to enable HandledExceptionCollector", exc_info=True)

    @classmethod
    def disable(cls) -> None:
        if cls._instance is None:
            log.debug("HandledExceptionCollector already disabled")
            return

        log.debug("Disabling HandledExceptionCollector")

        if sys.version_info >= (3, 12):
            from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _disable_monitoring

            _disable_monitoring()
