from dataclasses import dataclass
from typing import ClassVar
from typing import Optional

from ddtrace._trace.processor import SpanProcessor
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData

from ._iast_env import _get_iast_env
from ._iast_request_context import _iast_end_request
from ._iast_request_context_base import _iast_start_request


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

        _iast_start_request(span)
        inferred_proxy_span = core.current.get_item("inferred_proxy_span")
        if inferred_proxy_span is not None and span._parent is inferred_proxy_span:
            # The inferred parent starts analysis before the application span exists.
            # Transfer ownership once the actual request span starts.
            environment = _get_iast_env()
            if environment is not None and environment.span is inferred_proxy_span:
                environment.span = span

    def on_span_finish(self, span: SpanData) -> None:
        """Report reported vulnerabilities.

        Span Tags:
            - `_dd.iast.json`: Only when one or more vulnerabilities have been detected will we include the custom tag.
            - `_dd.iast.enabled`: Set to 1 when IAST is enabled in a request. If a request is disabled
              (e.g. by sampling), then it is not set.
        """
        if span.span_type != SpanTypes.WEB:
            return
        _iast_end_request(span)
