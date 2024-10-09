from dataclasses import dataclass

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger

from . import oce
from ._iast_request_context import set_iast_request_enabled
from ._iast_request_context import start_iast_context


log = get_logger(__name__)


@dataclass(eq=False)
class AppSecIastSpanProcessor(SpanProcessor):
    def __post_init__(self) -> None:
        from ddtrace.appsec import load_appsec

        load_appsec()

    def on_span_start(self, span: Span):
        if span.span_type not in {SpanTypes.WEB, SpanTypes.GRPC}:
            return

        start_iast_context()

        request_iast_enabled = False
        if oce.acquire_request(span):
            request_iast_enabled = True

        set_iast_request_enabled(request_iast_enabled)

    def on_span_finish(self, span: Span):
        """Report reported vulnerabilities.

        Span Tags:
            - `_dd.iast.json`: Only when one or more vulnerabilities have been detected will we include the custom tag.
            - `_dd.iast.enabled`: Set to 1 when IAST is enabled in a request. If a request is disabled
              (e.g. by sampling), then it is not set.
        """
        pass
