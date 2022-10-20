import json
from typing import TYPE_CHECKING

import attr

from ddtrace.appsec.iast import oce
from ddtrace.constants import IAST_CONTEXT_KEY
from ddtrace.constants import IAST_ENABLED
from ddtrace.constants import IAST_JSON
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.span import Span

log = get_logger(__name__)


@attr.s(eq=False)
class AppSecIastSpanProcessor(SpanProcessor):
    def on_span_start(self, span):
        # type: (Span) -> None
        if span.span_type != SpanTypes.WEB:
            return
        oce.acquire_request()

    def on_span_finish(self, span):
        # type: (Span) -> None
        """Report reported vulnerabilities.

        Span Tags:
            - `_dd.iast.json`: Only when one or more vulnerabilities have been detected will we include the custom tag.
            - `_dd.iast.enabled`: Set to 1 when IAST is enabled in a request. If a request is disabled
              (e.g. by sampling), then it is not set.
        """
        if span.span_type != SpanTypes.WEB:
            return

        span.set_metric(IAST_ENABLED, 1.0)

        data = _context.get_item(IAST_CONTEXT_KEY, span=span)

        if data:
            span.set_tag_str(IAST_JSON, json.dumps(attr.asdict(data)))

        oce.release_request()
