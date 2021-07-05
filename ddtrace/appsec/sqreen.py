from collections import Counter
import logging
from typing import Any
from typing import Mapping

from sq_native import waf  # type: ignore

from ddtrace.appsec.protections import BaseProtection


log = logging.getLogger(__name__)


class SqreenLibrary(BaseProtection):
    """
    Process application data with the Sqreen library.
    """

    budget = 5 * 1000  # max 5ms budget

    def __init__(self, rules):
        self._instance = waf.WAFEngine(rules)
        self.stats = Counter({"total": 0, "reported": 0})

    def process(self, context_id, data):
        # type: (int, Mapping[str, Any]) -> None
        log.debug("Create a new Sqreen context for %r", context_id)
        context = self._instance.create_context()
        ret = context.run(data, self.budget)
        log.debug("Sqreen context for %r returned: %r", context_id, ret)
        if ret.report:
            self.stats["reported"] += 1
        self.stats["total"] += 1
