import errno
import json
import os.path
from typing import TYPE_CHECKING

import attr

import ddtrace
from ddtrace.appsec._ddwaf import DDWaf
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor
from ddtrace.utils.formats import get_env


if TYPE_CHECKING:
    from ddtrace import Span

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(ROOT_DIR, "rules.json")

log = get_logger(__name__)


def get_rules():
    return get_env("appsec", "rules", default=DEFAULT_RULES)


@attr.s(eq=False)
class AppSecSpanProcessor(SpanProcessor):

    rules = attr.ib(type=str, factory=get_rules)
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
                if err.errno == errno.ENOENT:
                    log.error(
                        "[DDAS-0001-03] AppSec could not read the rule file %s. Reason: file does not exist", self.rules
                    )
                else:
                    # TODO: try to log reasons
                    log.error("[DDAS-0001-03] AppSec could not read the rule file %s.", self.rules)
                raise
            except json.decoder.JSONDecodeError:
                log.error(
                    "[DDAS-0001-03] AppSec could not read the rule file %s. Reason: invalid JSON file", self.rules
                )
                raise
            except Exception:
                # TODO: try to log reasons
                log.error("[DDAS-0001-03] AppSec could not read the rule file %s.", self.rules)
                raise
            try:
                self._ddwaf = DDWaf(rules)
            except ValueError:
                # Partial of DDAS-0005-00
                log.warning("[DDAS-0005-00] WAF initialization failed")
                raise

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        if span.span_type != SpanTypes.WEB.value:
            return
        span.set_metric("_dd.appsec.enabled", 1.0)
        span._set_str_tag("_dd.runtime_family", "python")
        data = {
            "server.request.uri.raw": span.get_tag(ddtrace.ext.http.URL),
            "server.response.status": span.get_tag(ddtrace.ext.http.STATUS_CODE),
        }
        log.debug("[DDAS-001-00] Executing AppSec In-App WAF with parameters: %s", data)
        res = self._ddwaf.run(data)  # res is a serialized json
        if res is not None:
            # Partial DDAS-011-00
            log.debug("[DDAS-011-00] AppSec In-App WAF returned: %s", res)
            span.meta["appsec.event"] = "true"
            span.meta["_dd.appsec.json"] = '{"triggers":%s}' % (res,)
            span.set_tag(MANUAL_KEEP_KEY)
