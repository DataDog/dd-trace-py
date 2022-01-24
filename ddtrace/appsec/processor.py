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
from ddtrace.internal.logger import get_logger
from ddtrace.utils.formats import get_env


if TYPE_CHECKING:
    from ddtrace import Span

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(ROOT_DIR, "rules.json")

log = get_logger(__name__)


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
        if span.span_type is not None and span.span_type == "web":
            # Insert ourself before the tracer's span processors
            span._on_finish_callbacks.insert(0, self.on_span_finish)

    def on_span_finish(self, span):
        # type: (Span) -> None
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
