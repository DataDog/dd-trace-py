"""
The AppSec module detects security anomalies from inside the application.

Test it with::

    DD_APPSEC_ENABLED=true FLASK_APP=hello.py ddtrace-run flask run

"""
import logging
import os.path
from typing import Any
from typing import List
from typing import Mapping

import attr

from ddtrace import Span
from ddtrace.appsec.internal.protections import BaseProtection
from ddtrace.utils.formats import get_env


log = logging.getLogger(__name__)


@attr.s(eq=False)
class Management(object):
    """
    AppSec module management.
    """

    protections = attr.ib(type=List[BaseProtection], default=[])

    def enable(self):
        # type: () -> None
        """Enable the AppSec module and load static protections."""

        root_dir = os.path.dirname(os.path.abspath(__file__))
        default_rules = os.path.join(root_dir, "rules.json")
        path = get_env("appsec", "rules", default=default_rules)

        sqreen_budget_ms = get_env("appsec", "sqreen", "budget_ms")

        try:
            from ddtrace.appsec.internal.sqreen import SqreenLibrary

            with open(path, "r") as f:
                rules = f.read()

            self.protections = [SqreenLibrary(rules, float(sqreen_budget_ms) if sqreen_budget_ms is not None else None)]
        except Exception:
            log.warning(
                "AppSec module failed to load. Your application is not protected. "
                "Please report this issue to support@datadoghq.com",
                exc_info=True,
            )
        else:
            log.info("AppSec module is enabled. Your application is protected.")

    def disable(self):
        # type: () -> None
        """Disable the AppSec module and unload protections."""
        self.protections = []
        log.warning("AppSec module is disabled. Your application is not protected anymore.")

    def process_request(self, span, **data):
        # type: (Span, Mapping[str, Any]) -> None
        """Process HTTP request data emitted by the integration hooks."""
        for protection in self.protections:
            protection.process(span, data)
