import sys
import typing as t

from ddtrace._trace.span import Span
from ddtrace.errortracking._handled_exceptions.events import SpanEventData
from ddtrace.errortracking._handled_exceptions.events import capture_exception_event
from ddtrace.errortracking._handled_exceptions.events import clear_exception_events
from ddtrace.errortracking._handled_exceptions.events import get_exception_events
from ddtrace.errortracking._handled_exceptions.events import on_span_exception
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.internal.settings.errortracking import config


log = get_logger(__name__)


class HandledExceptionCollector(Service):
    _instance: t.Optional["HandledExceptionCollector"] = None

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
        core.on("span.exception", on_span_exception)

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
    def capture_exception_event(cls, span: Span, exc: Exception, event: SpanEventData):
        capture_exception_event(span, exc, event)

    @classmethod
    def get_exception_events(cls, span_id: int):
        return get_exception_events(span_id)

    @classmethod
    def clear_exception_events(cls, span_id: int):
        clear_exception_events(span_id)
