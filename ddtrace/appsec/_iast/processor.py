from dataclasses import dataclass
from typing import ClassVar
from typing import Optional

from ddtrace._trace.processor import SpanProcessor
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData

from ._iast_request_context import _iast_end_request
from ._iast_request_context_base import _iast_start_request
from ._request_lifecycle import _SPAN_ROUTES
from ._request_lifecycle import find_request_owner
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
        if span.span_type != SpanTypes.WEB:
            return
        uses_events = uses_request_events(core.current)
        _SPAN_ROUTES[span] = uses_events
        if uses_events:
            return
        _iast_start_request(span)

    def on_span_finish(self, span: SpanData) -> None:
        """Report reported vulnerabilities.

        Span Tags:
            - `_dd.iast.json`: Only when one or more vulnerabilities have been detected will we include the custom tag.
            - `_dd.iast.enabled`: Set to 1 when IAST is enabled in a request. If a request is disabled
              (e.g. by sampling), then it is not set.
        """
        if span.span_type != SpanTypes.WEB:
            return
        uses_events = _SPAN_ROUTES.pop(span, None)
        if uses_events is True or (uses_events is None and find_request_owner(core.current)):
            # Request events also own children typed WEB after span start.
            return
        _iast_end_request(span)
