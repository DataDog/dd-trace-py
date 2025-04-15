import sys
import typing as t

from ddtrace._trace.span import Span
from ddtrace._trace.span import SpanEvent
from ddtrace.internal.constants import SPAN_EVENTS_HAS_EXCEPTION
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.settings.errortracking import config


log = get_logger(__name__)


def _add_span_events(span: Span) -> None:
    """
    If the same error is handled/rethrown multiple times, we want
    to report only one span events. Therefore, we do not add directly
    a span event for every handled exceptions, we store them in the span
    and add them when the span finishes.
    """
    span_exc_events = list(HandledExceptionCollector.get_exception_events(span.span_id).values())
    if span_exc_events:
        span.set_tag_str(SPAN_EVENTS_HAS_EXCEPTION, "true")
        span._events.extend(span_exc_events)
    HandledExceptionCollector.clear_exception_events(span.span_id)


class HandledExceptionCollector(Service):
    _instance: t.Optional["HandledExceptionCollector"] = None
    _span_exception_events: t.Dict[int, t.Dict[Exception, SpanEvent]] = {}

    def __init__(self) -> None:
        super(HandledExceptionCollector, self).__init__()
        log.debug("%s initialized", self.__class__.__name__)

    @classmethod
    def enable(cls) -> None:
        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        log.debug("Enabling %s", cls.__name__)
        cls._instance = cls()
        cls._instance.start()

        log.debug("%s enabled", cls.__name__)

    @classmethod
    def disable(cls) -> None:
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return

        log.debug("Disabling %s", cls.__name__)
        cls._instance.stop()
        cls._instance = None
        log.debug("%s disabled", cls.__name__)

    def _start_service(self) -> None:
        try:
            if config.enabled is False:
                return
            if sys.version_info >= (3, 12):
                from ddtrace.errortracking._handled_exceptions.monitoring_reporting import (
                    _install_sys_monitoring_reporting,
                )

                """
                Starting from python3.12, handled exceptions reporting is based on sys.monitoring. This is
                safer than bytecode injection as it can not alter the behaviour of a program. However,
                sys.monitoring reports every handled exceptions including python internal ones. Therefore,
                we need to add a filtering step which can be time efficient.
                """
                _install_sys_monitoring_reporting()
            elif sys.version_info >= (3, 10):
                from ddtrace.errortracking._handled_exceptions.bytecode_reporting import (
                    _install_bytecode_injection_reporting,
                )

                """
                For python3.10 and python3.11, handled exceptions reporting is based on bytecode injection.
                This is efficient, as we will instrument only the targeted code. However, it is considered
                unsafe as it could alter the program behavior.Therefore, it will drop for sys.monitoring in
                3.12 (before handled exception events are not supported)
                """
                _install_bytecode_injection_reporting()
            else:
                return
        except Exception:
            log.error("Failed to enable HandledExceptionCollector", exc_info=True)

    def _stop_service(self) -> None:
        if sys.version_info >= (3, 12):
            from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _disable_monitoring

            _disable_monitoring()

    @classmethod
    def capture_exception_event(cls, span: Span, exc: Exception, event: SpanEvent):
        span_id = span.span_id
        events_dict = cls._span_exception_events.setdefault(span_id, {})
        if not events_dict:
            span._add_on_finish_exception_callback(_add_span_events)
        events_dict[exc] = event

    @classmethod
    def get_exception_events(cls, span_id: int):
        return cls._span_exception_events.get(span_id, {})

    @classmethod
    def clear_exception_events(cls, span_id: int):
        if span_id in cls._span_exception_events:
            del cls._span_exception_events[span_id]
