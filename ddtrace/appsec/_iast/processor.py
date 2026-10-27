from dataclasses import dataclass
from typing import ClassVar
from typing import Optional

from ddtrace._trace.processor import SpanProcessor
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.inferred_spans import is_inferred_span
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData

from ._iast_request_context import _iast_end_request
from ._iast_request_context_base import _iast_start_request
from ._request_lifecycle import _SPAN_ROUTES
from ._request_lifecycle import uses_request_events


log = get_logger(__name__)


@dataclass(eq=False)
class AppSecIastSpanProcessor(SpanProcessor):
    _instance: ClassVar[Optional["AppSecIastSpanProcessor"]] = None

    @classmethod
    def enable(cls) -> None:
        """Enable the IAST span processor."""
        if cls._instance is None:
            instance = cls._instance = cls()
            instance.register()

    @classmethod
    def disable(cls) -> None:
        """Disable the IAST span processor."""
        if cls._instance is not None:
            cls._instance.unregister()
            cls._instance = None

    def __post_init__(self) -> None:
        from . import load_iast

        load_iast()

    def on_span_start(self, span: SpanData) -> None:
        if span.span_type != SpanTypes.WEB or is_inferred_span(span):
            return
        if uses_request_events(core.current, span):
            _SPAN_ROUTES[span] = True
            return
        _iast_start_request(span)

    def on_span_finish(self, span: SpanData) -> None:
        """Report requests that have not migrated to lifecycle events."""
        if span.span_type != SpanTypes.WEB or is_inferred_span(span):
            return
        if _SPAN_ROUTES and _SPAN_ROUTES.pop(span, False):
            return
        _iast_end_request(span)
