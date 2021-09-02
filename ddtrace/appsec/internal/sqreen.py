from typing import Any
from typing import Mapping
from typing import Optional
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ddtrace import Span

from sq_native import waf

from ddtrace.appsec.internal.protections import BaseProtection
from ddtrace.internal import logger
from ddtrace.utils.formats import get_env
from ddtrace.utils.time import StopWatch


log = logger.get_logger(__name__)

DEFAULT_WAF_BUDGET_MS = 5.0


class SqreenLibrary(BaseProtection):
    """
    Process application data with the Sqreen library.
    """

    def __init__(self, rules, budget_ms=None):
        # type: (str, Optional[float]) -> None
        if budget_ms is None:
            budget_ms = float(
                get_env("appsec", "waf_budget_ms", default=DEFAULT_WAF_BUDGET_MS)  # type: ignore[arg-type]
            )
        self._budget = int(budget_ms * 1000)
        self._instance = waf.WAFEngine(rules)

    def process(self, span, data):
        # type: (Span, Mapping[str, Any]) -> None
        # DEV: Headers require special transformation as the integrations don't
        # do it yet (implemented in https://github.com/DataDog/dd-trace-py/pull/2762)
        headers = {k.lower(): v for k, v in data.get("headers", {}).items()}
        data = dict(data)
        data["headers"] = headers
        with StopWatch() as timer:
            context = self._instance.create_context()
            ret = context.run(data, self._budget)
            del context
        elapsed_ms = timer.elapsed() * 1000
        log.debug("context returned %r in %.5fms for %r", ret, elapsed_ms, span)
        span.set_metric("_dd.appsec.waf_eval_ms", elapsed_ms)
        if ret.timeout:
            span.set_metric("_dd.appsec.waf_overtime_ms", elapsed_ms - self._budget)
