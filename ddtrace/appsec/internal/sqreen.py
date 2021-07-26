from datetime import datetime
import json
import logging
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import TYPE_CHECKING
import uuid


if TYPE_CHECKING:
    from ddtrace import Span

from sq_native import waf

from ddtrace.appsec.internal.events.attack import Attack_0_1_0
from ddtrace.appsec.internal.events.attack import Rule
from ddtrace.appsec.internal.events.attack import RuleMatch
from ddtrace.appsec.internal.events.context import HttpRequest
from ddtrace.appsec.internal.events.context import Http_0_1_0
from ddtrace.appsec.internal.events.context import get_required_context
from ddtrace.appsec.internal.protections import BaseProtection
from ddtrace.appsec.internal.utils import event_rules_to_sqreen
from ddtrace.internal.compat import utc
from ddtrace.utils.http import strip_query_string
from ddtrace.utils.time import StopWatch


log = logging.getLogger(__name__)

DEFAULT_SQREEN_BUDGET_MS = 5.0


class SqreenLibrary(BaseProtection):
    """
    Process application data with the Sqreen library.
    """

    def __init__(self, rules, budget_ms=None):
        # type: (str, Optional[float]) -> None
        if budget_ms is None:
            budget_ms = DEFAULT_SQREEN_BUDGET_MS
        self._budget = int(budget_ms * 1000)
        self._instance = waf.WAFEngine(rules)

    def process(self, span, data):
        # type: (Span, Mapping[str, Any]) -> Sequence[Attack_0_1_0]
        with StopWatch() as timer:
            context = self._instance.create_context()
            ret = context.run(data, self._budget)
        elapsed_ms = timer.elapsed() * 1000
        log.debug("Sqreen context returned %r in %.5fms for %r", ret, elapsed_ms, span)
        span.set_metric("_dd.sq.process_ms", elapsed_ms)
        if elapsed_ms > self._budget:
            span.set_metric("_dd.sq.overtime_ms", elapsed_ms - self._budget)
        if ret.report:
            span.set_metric("_dd.sq.reports", 1)
            context = get_required_context(actor_ip=data.get("remote_ip"))
            context.http = Http_0_1_0(
                request=HttpRequest(
                    scheme="http",
                    method=data.get("method") or "",
                    url=strip_query_string(data.get("target") or ""),
                    host="",
                    port=0,
                    path="",
                    remote_ip=data.get("remote_ip") or "",
                    remote_port=0,
                )
            )
            return [self.sqreen_waf_to_attack(ret.data, context=context, blocked=ret.block)]
        return []

    @staticmethod
    def sqreen_waf_to_attack(data, context=None, blocked=False, at=None):
        """Convert a Sqreen WAF result to an AppSec Attack event."""
        if at is None:
            at = datetime.now(utc)
        waf_data = json.loads(data.decode("utf-8", errors="replace"))
        filter_data = waf_data[0]["filter"][0]
        return Attack_0_1_0(
            event_id=str(uuid.uuid4()),
            detected_at=at.isoformat(),
            type="waf",
            blocked=blocked,
            rule=Rule(id=waf_data[0]["rule"], name=waf_data[0]["flow"], set="waf"),
            rule_match=RuleMatch(
                operator=filter_data["operator"],
                operator_value=filter_data["operator_value"],
                parameters=[],  # DEV: do not report user data yet
                highlight=[],  # DEV: do not report user data yet
                has_server_side_match=False,
            ),
            context=context or get_required_context(),
        )

    @classmethod
    def from_event_rules(cls, rules, budget_ms=None):
        """Load the Sqreen library from event rules."""
        return cls(event_rules_to_sqreen(rules), budget_ms=budget_ms)
