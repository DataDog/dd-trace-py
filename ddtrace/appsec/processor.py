import errno
import json
import os.path
import threading
from typing import TYPE_CHECKING

import attr

from ddtrace.appsec._ddwaf import DDWaf
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor
from ddtrace.utils.formats import get_env


if TYPE_CHECKING:
    from ddtrace import Span

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(ROOT_DIR, "rules.json")

log = get_logger(__name__)


def get_rule_file():
    return get_env("appsec", "rules", default=DEFAULT_RULES)


@attr.s(eq=False)
class AppSecSpanProcessor(SpanProcessor):

    _lock = attr.ib(init=False, factory=threading.Lock, repr=False)

    rules = attr.ib(type=str, factory=get_rule_file)
    _ddwaf = attr.ib(type=DDWaf, default=None)

    @property
    def enabled(self):
        return self._ddwaf is not None

    def __attrs_post_init__(self):
        # type: () -> None
        if self._ddwaf is None:
            try:
                with open(self.rules, "r") as f:
                    rules = json.load(f)
            except EnvironmentError as err:
                # DDAS-0001-03
                if err.errno == errno.ENOENT:
                    log.error("AppSec could not read the rule file %s. Reason: file does not exist", self.rules)
                else:
                    # DDAS-0001-03 - TODO: try to log reasons
                    log.error("AppSec could not read the rule file %s.", self.rules)
                raise
            except json.decoder.JSONDecodeError:
                # DDAS-0001-03
                log.error("AppSec could not read the rule file %s. Reason: invalid JSON file", self.rules)
                raise
            except Exception:
                # DDAS-0001-03 - TODO: try to log reasons
                log.error("AppSec could not read the rule file %s.", self.rules)
                raise
            try:
                self._ddwaf = DDWaf(rules)
            except ValueError:
                # Partial of DDAS-0005-00
                log.warning("WAF initialization failed")
                raise

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        with self._lock:
            if span.span_type is None or span.span_type != "web":
                return
            span.set_metric("_dd.appsec.enabled", 1.0)
            span.set_tag("_dd.runtime_family", "python")
            data = {
                "server.request.uri.raw": span.get_tag("http.url"),
                "server.response.status": span.get_tag("http.status_code"),
            }
            # DDAS-001-00
            log.debug("Executing AppSec In-App WAF with parameters: %s", data)
            res = self._ddwaf.run(data)
            if res is not None:
                # Partial DDAS-011-00
                log.debug("AppSec In-App WAF returned: %s", res)
                span.meta["appsec.event"] = "true"
                span.meta["_dd.appsec.json"] = '{"triggers":%s}' % (res,)
                span.set_tag(MANUAL_KEEP_KEY)
