"""
The AppSec module detects security anomalies from inside the application.

Test it with::

    DD_APPSEC_ENABLED=true FLASK_APP=hello.py ddtrace-run flask run

"""
import os.path
from typing import Any
from typing import List

import attr

from ddtrace import Span
from ddtrace import config
from ddtrace.appsec.internal.protections import BaseProtection
from ddtrace.internal import logger
from ddtrace.utils.formats import get_env


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(ROOT_DIR, "rules.json")

log = logger.get_logger(__name__)


@attr.s(eq=False)
class Management(object):
    """
    AppSec module management.
    """

    protections = attr.ib(type=List[BaseProtection], default=[])

    def enable(self):
        # type: () -> None
        """Enable the AppSec module and load static protections."""

        try:
            path = get_env("appsec", "rules", default=DEFAULT_RULES)
            with open(str(path), "r") as f:
                rules = f.read()
        except Exception as e:
            log.warning(
                "AppSec module failed to load rules. Your application is not protected.",
                exc_info=True,
            )
            if config._raise:
                raise e

        try:
            from ddtrace.appsec.internal.sqreen import SqreenLibrary

            self.protections = [SqreenLibrary(rules)]
        except Exception as e:
            log.warning(
                "AppSec module failed to load. Your application is not protected. "
                "Please report this issue to support@datadoghq.com",
                exc_info=True,
            )
            if config._raise:
                raise e
        else:
            log.info("AppSec module is enabled. Your application is protected.")

    def disable(self):
        # type: () -> None
        """Disable the AppSec module and unload protections."""
        self.protections = []
        log.warning("AppSec module is disabled. Your application is not protected anymore.")

    def process_request(self, span, **data):
        # type: (Span, Any) -> None
        """Process HTTP request data emitted by the integration hooks."""
        for protection in self.protections:
            protection.process(span, data)
