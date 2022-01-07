import errno
import json
import os.path
from typing import Any
from typing import ClassVar
from typing import Optional
from typing import TYPE_CHECKING

import attr

import ddtrace
from ddtrace import config
from ddtrace.appsec._ddwaf import DDWaf
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.gateway import gateway
from ddtrace.gateway.gateway import Subscription
from ddtrace.internal.logger import get_logger
from ddtrace.utils.formats import get_env


if TYPE_CHECKING:
    from ddtrace import Span

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(ROOT_DIR, "rules.json")

log = get_logger(__name__)


class WafSubscription(Subscription):
    def __init__(self, ddwaf):
        self._ddwaf = ddwaf

    def run(self, store, new_addresses):
        if "waf_context" not in store["meta"]:
            store["meta"]["waf_context"] = self._ddwaf.create_waf_context()
            store["meta"]["appsec_events"] = []
        waf_context = store["meta"]["waf_context"]
        if waf_context.disposed:
            return None
        data = {}
        for key in new_addresses:
            data[key] = store["addresses"][key]
        log.debug("[DDAS-001-00] Executing AppSec In-App WAF with parameters: %s", data)
        res = waf_context.run(data)
        if res["data"] is not None:
            log.debug("[DDAS-011-00] AppSec In-App WAF returned: %s", res)
            store["meta"]["appsec_events"] += json.loads(res["data"])


@attr.s(eq=False)
class AppSecProcessor(object):

    tracer = attr.ib(type=ddtrace.Tracer, default=ddtrace.tracer)
    rules = attr.ib(type=str, factory=lambda: get_env("appsec", "rules", default=DEFAULT_RULES))
    _ddwaf = attr.ib(type=DDWaf, default=None)

    enabled = False  # type: ClassVar[bool]
    _instance = None  # type: ClassVar[Optional[AppSecProcessor]]

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

    @classmethod
    def enable(cls, *args, **kwargs):
        # type: (Any, Any) -> None
        if cls._instance is not None:
            return

        try:
            processor = cls(*args, **kwargs)
        except Exception:
            log.warning("AppSec module failed to load.", exc_info=True)
            if config._raise:
                raise
            return

        needed_address = processor._ddwaf.required_data
        subscription = WafSubscription(processor._ddwaf)
        gateway.subscribe(needed_address, subscription)
        # Automatically enable the AppSec processor on the tracer
        processor.tracer.on_start_span(processor._attach_web_span)
        cls._instance = processor
        cls.enabled = True
        log.info("AppSec module is enabled.")

    @classmethod
    def disable(cls):
        # type: () -> None
        if cls._instance is None:
            return
        # Will only disable AppSec for new spans
        cls._instance.tracer.deregister_on_start_span(cls._instance._attach_web_span)
        cls._instance = None
        cls.enabled = False

    def _attach_web_span(self, span):
        # type: (Span) -> None
        if span.span_type is not None and span is self.tracer.current_root_span():
            span.set_metric("_dd.appsec.enabled", 1.0)
            span.set_tag("_dd.runtime_family", "python")
            # Insert ourself before the tracer's span processors
            span._on_finish_callbacks.insert(0, self.on_span_finish)

    def on_span_finish(self, span):
        # type: (Span) -> None
        data = {
            "server.request.uri.raw": span.get_tag("http.url"),
            "server.response.status": span.get_tag("http.status_code"),
        }
        gateway.propagate(span.store, data)
        span.store["meta"]["waf_context"].dispose()
        if len(span.store["meta"]["appsec_events"]) > 0:
            span.set_tag("appsec.event", "true")
            span.set_tag("_dd.appsec.json", '{"triggers":%s}' % (json.dumps(span.store["meta"]["appsec_events"]),))
            span.set_tag(MANUAL_KEEP_KEY)
