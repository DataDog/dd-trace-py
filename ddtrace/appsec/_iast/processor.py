from dataclasses import dataclass
from typing import Optional

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.appsec import load_appsec
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import IAST
from ddtrace.constants import ORIGIN_KEY
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger

from .._trace_utils import _asm_manual_keep
from . import oce
from ._iast_request_context import end_iast_context
from ._iast_request_context import get_iast_reporter
from ._iast_request_context import is_iast_request_enabled
from ._iast_request_context import set_iast_request_enabled
from ._iast_request_context import start_iast_context
from ._metrics import _set_metric_iast_request_tainted
from ._metrics import _set_span_tag_iast_executed_sink
from ._metrics import _set_span_tag_iast_request_tainted
from .reporter import IastSpanReporter


log = get_logger(__name__)


@dataclass(eq=False)
class AppSecIastSpanProcessor(SpanProcessor):
    def __post_init__(self) -> None:
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
        if span.span_type not in {SpanTypes.WEB, SpanTypes.GRPC}:
            return

        if not is_iast_request_enabled():
            span.set_metric(IAST.ENABLED, 0.0)
            end_iast_context(span)
            return

        span.set_metric(IAST.ENABLED, 1.0)

        report_data: Optional[IastSpanReporter] = get_iast_reporter()

        if report_data:
            report_data.build_and_scrub_value_parts()
            span.set_tag_str(IAST.JSON, report_data._to_str())
            _asm_manual_keep(span)

        _set_metric_iast_request_tainted()
        _set_span_tag_iast_request_tainted(span)
        _set_span_tag_iast_executed_sink(span)
        end_iast_context(span)

        if span.get_tag(ORIGIN_KEY) is None:
            span.set_tag_str(ORIGIN_KEY, APPSEC.ORIGIN_VALUE)

        oce.release_request()
