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
from ddtrace import config
from ddtrace.appsec.internal.events import Event
from ddtrace.appsec.internal.protections import BaseProtection
from ddtrace.appsec.internal.writer import BaseEventWriter
from ddtrace.appsec.internal.writer import HTTPEventWriter
from ddtrace.appsec.internal.writer import NullEventWriter
from ddtrace.internal import agent
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.utils.formats import get_env


log = logging.getLogger(__name__)


@attr.s(eq=False)
class Management(object):
    """
    AppSec module management.
    """

    protections = attr.ib(type=List[BaseProtection], default=[])
    writer = attr.ib(type=BaseEventWriter, init=False, default=NullEventWriter())

    def enable(self):
        """Enable the AppSec module and load static protections."""

        if config.health_metrics_enabled:
            dogstatsd = get_dogstatsd_client(agent.get_stats_url())
        else:
            dogstatsd = None

        root_dir = os.path.dirname(os.path.abspath(__file__))
        default_rules = os.path.join(root_dir, "rules.yaml")
        path = get_env("appsec", "rules", default=default_rules)

        sqreen_budget_ms = get_env("appsec", "sqreen", "budget_ms")

        try:
            from ddtrace.appsec.internal.sqreen import SqreenLibrary

            with open(path, "r") as f:
                event_rules = f.read()

            self.protections = [
                SqreenLibrary.from_event_rules(
                    event_rules, float(sqreen_budget_ms) if sqreen_budget_ms is not None else None
                )
            ]
            self.writer.flush(timeout=0)
            self.writer = HTTPEventWriter(api_key=get_env("api_key"), dogstatsd=dogstatsd)
        except Exception:
            log.warning(
                "AppSec module failed to load. Your application is not protected. "
                "Please report this issue on https://github.com/DataDog/dd-trace-py/issues",
                exc_info=True,
            )
        else:
            log.info("AppSec module is enabled. Your application is protected.")

    def disable(self):
        """Disable the AppSec module and unload protections."""
        self.protections = []
        self.writer.flush(timeout=0)
        self.writer = NullEventWriter()
        log.warning("AppSec module is disabled. Your application is not protected anymore.")

    def process_request(self, span, **data):
        # type: (Span, Mapping[str, Any]) -> None
        """Process HTTP request data emitted by the integration hooks."""
        events = []  # type: List[Event]
        for protection in self.protections:
            events.extend(protection.process(span, data))
        if events:
            self.writer.write(events)
