import logging
from typing import Any
from typing import Mapping
from typing import Optional
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ddtrace import Span

from sq_native import waf

from ddtrace.appsec.internal.protections import BaseProtection
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
        # type: (Span, Mapping[str, Any]) -> None
        with StopWatch() as timer:
            context = self._instance.create_context()
            ret = context.run(data, self._budget)
        elapsed_ms = timer.elapsed() * 1000
        log.debug("Sqreen context returned %r in %.5fms for %r", ret, elapsed_ms, span)
        span.set_metric("sq.process_ms", elapsed_ms)
        if elapsed_ms > self._budget:
            span.set_metric("sq.overtime_ms", elapsed_ms - self._budget)
        if ret.report:
            span.set_metric("sq.reports", 1)
