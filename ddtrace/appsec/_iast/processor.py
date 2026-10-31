from dataclasses import dataclass
from typing import ClassVar
from typing import Optional

from ddtrace._trace.processor import SpanProcessor
from ddtrace.appsec._iast_context import _get_iast_context_id
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.inferred_spans import is_inferred_span
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData

from ._iast_env import _get_iast_env
from ._iast_request_context import _iast_end_request
from ._iast_request_context_base import _iast_finish_request
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
            core.on("trace.span_discard", instance.on_span_discard)

    @classmethod
    def disable(cls) -> None:
        """Disable the IAST span processor."""
        if cls._instance is not None:
            cls._instance.unregister()
            core.reset_listeners("trace.span_discard", cls._instance.on_span_discard)
            cls._instance = None

    def __post_init__(self) -> None:
        from . import load_iast

        load_iast()

    def on_span_start(self, span: SpanData) -> None:
        if span.span_type != SpanTypes.WEB or is_inferred_span(span):
            return

        _iast_start_request(span)

    def on_span_finish(self, span: SpanData) -> None:
        """Report reported vulnerabilities.

        Span Tags:
            - `_dd.iast.json`: Only when one or more vulnerabilities have been detected will we include the custom tag.
            - `_dd.iast.enabled`: Set to 1 when IAST is enabled in a request. If a request is disabled
              (e.g. by sampling), then it is not set.
        """
        if span.span_type != SpanTypes.WEB or is_inferred_span(span):
            return
        _iast_end_request(span)

    def on_span_discard(self, span: SpanData) -> None:
        if span.span_type != SpanTypes.WEB:
            return
        env = _get_iast_env()
        # A late discard must not retire a newer request's context or an inherited environment.
        if env is not None and env.span is span and env.context_id == _get_iast_context_id():
            _iast_finish_request(span, shoud_update_global_vulnerability_limit=False)
