from collections import Counter
import logging
from typing import Any
from typing import Mapping
from typing import Optional

from sq_native import waf

from ddtrace.appsec.internal.protections import BaseProtection


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
        self.stats = Counter({"total": 0, "reported": 0})

    def process(self, context_id, data):
        # type: (int, Mapping[str, Any]) -> None
        log.debug("Create a new Sqreen context for %r", context_id)
        context = self._instance.create_context()
        ret = context.run(data, self._budget)
        log.debug("Sqreen context for %r returned: %r", context_id, ret)
        if ret.report:
            self.stats["reported"] += 1
        self.stats["total"] += 1
