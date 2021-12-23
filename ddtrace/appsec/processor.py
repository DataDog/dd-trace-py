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

    tracer = attr.ib(type=ddtrace.Tracer, default=None)
    rules = attr.ib(type=str, default=None)
    _ddwaf = attr.ib(type=DDWaf, init=False)

    enabled = False
    _instance = None  # type: ClassVar[Optional[AppSecProcessor]]

    def __attrs_post_init__(self):
        # type: () -> None
        if self.tracer is None:
            self.tracer = ddtrace.tracer
        if self.rules is None:
            self.rules = get_env("appsec", "rules", default=DEFAULT_RULES)
        if self._ddwaf is None:
            with open(self.rules, "r") as f:
                rules = json.load(f)
            self._ddwaf = DDWaf(rules)

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
        data = {
            "server.request.uri.raw": span.get_tag("http.url"),
            "server.response.status": span.get_tag("http.status_code"),
        }
        res = self._ddwaf.run(data)
        if res is not None:
            span.set_tag("appsec.event", "true")
            span.set_tag("_dd.appsec.json", '{"triggers":%s}' % (res,))
            span.set_tag(MANUAL_KEEP_KEY)
