import json
from typing import TYPE_CHECKING

import attr

from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._metrics import _set_metric_iast_request_tainted
from ddtrace.appsec.iast._util import _is_iast_enabled
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import ORIGIN_KEY
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
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
        oce.acquire_request(span)
        from ddtrace.appsec.iast._taint_tracking import create_context

        create_context()

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

        if not oce._enabled or not _is_iast_enabled():
            span.set_metric(IAST.ENABLED, 0.0)
            return

        from ddtrace.appsec.iast._taint_tracking import contexts_reset  # noqa: F401

        span.set_metric(IAST.ENABLED, 1.0)

        data = core.get_item(IAST.CONTEXT_KEY, span=span)

        if data:
            from ddtrace.appsec.iast._taint_tracking import OriginType  # noqa: F401
            from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import origin_to_str  # noqa: F401

            class OriginTypeEncoder(json.JSONEncoder):
                def default(self, obj):
                    if isinstance(obj, OriginType):
                        # if the obj is uuid, we simply return the value of uuid
                        return origin_to_str(obj)
                    return json.JSONEncoder.default(self, obj)

            span.set_tag_str(
                IAST.JSON,
                json.dumps(attr.asdict(data, filter=lambda attr, x: x is not None), cls=OriginTypeEncoder),
            )
            span.set_tag(MANUAL_KEEP_KEY)

        _set_metric_iast_request_tainted()
        contexts_reset()

        if span.get_tag(ORIGIN_KEY) is None:
            span.set_tag_str(ORIGIN_KEY, APPSEC.ORIGIN_VALUE)

        oce.release_request()
