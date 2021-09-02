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

    _protections = attr.ib(type=List[BaseProtection], init=False)

    @property
    def enabled(self):
        # type: () -> bool
        """Returns True if the AppSec module is enabled and was correctly loaded."""
        return bool(self._protections)

    def enable(self):
        # type: () -> None
        """Enable the AppSec module and load static protections."""

        try:
            path = get_env("appsec", "rules", default=DEFAULT_RULES)
            with open(path, "r") as f:  # type: ignore[arg-type]
                rules = f.read()
        except Exception as e:
            log.warning(
                "AppSec module failed to load rules.",
                exc_info=True,
            )
            if config._raise:
                raise e
            return

        try:
            from ddtrace.appsec.internal.sqreen import SqreenLibrary

            self._protections = [SqreenLibrary(rules)]
        except Exception as e:
            log.warning(
                "AppSec module failed to load. "
                "Please report this issue to support@datadoghq.com",
                exc_info=True,
            )
            if config._raise:
                raise e
        else:
            log.info("AppSec module is enabled.")

    def disable(self):
        # type: () -> None
        """Disable the AppSec module and unload protections."""
        self._protections = []
        log.warning("AppSec module is disabled.")

    def process_request(self, span, **data):
        # type: (Span, Any) -> None
        """Process HTTP request data emitted by the integration hooks."""
        for protection in self._protections:
            protection.process(span, data)
