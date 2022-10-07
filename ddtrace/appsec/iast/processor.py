import json
from typing import TYPE_CHECKING

import attr

from ddtrace.constants import IAST_CONTEXT_KEY
from ddtrace.constants import IAST_ENABLED
from ddtrace.constants import IAST_JSON
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from ddtrace.internal.processor import SpanProcessor


if TYPE_CHECKING:
    from ddtrace.span import Span


@attr.s(eq=False)
class AppSecIastSpanProcessor(SpanProcessor):
    def on_span_start(self, span):
        # type: (Span) -> None
        pass

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

        data = _context.get_item(IAST_CONTEXT_KEY, span=span)

        span.set_tag(IAST_ENABLED, 1)

        if data:
            span.set_tag(IAST_JSON, json.dumps(attr.asdict(data)))
